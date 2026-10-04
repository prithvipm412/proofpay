// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import { Test } from "forge-std/Test.sol";
import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";
import { ProofPayEscrow } from "../contracts/ProofPayEscrow.sol";

/// @notice M1 tests: C-01 to C-15 and C-23. The full C-T1 to C-T14 set comes in M2.
contract ProofPayEscrowTest is Test {
    ProofPayEscrow internal escrow;

    address internal owner = makeAddr("owner");
    address internal verifier = makeAddr("verifier");
    address internal poster = makeAddr("poster");
    address internal worker = makeAddr("worker");
    address internal stranger = makeAddr("stranger");

    uint64 internal constant DISPUTE_WINDOW = 60;
    uint64 internal constant REVIEW_GRACE = 300;
    uint64 internal constant ARBITRATION_TIMEOUT = 3600;

    uint256 internal constant AMOUNT = 0.05 ether;
    bytes32 internal constant BEFORE = keccak256("before photo");
    bytes32 internal constant PROOF = keccak256("after photo");
    string internal constant TITLE = "Remove the litter around this bench";
    string internal constant DESCRIPTION = "Pick up all litter on the grass around the bench.";

    event TaskCreated(
        uint256 indexed id, address indexed poster, uint256 amount, bytes32 beforeHash, uint64 submitBy, uint64 reviewBy
    );
    event TaskAccepted(uint256 indexed id, address indexed worker);
    event ProofSubmitted(uint256 indexed id, uint8 attempt, bytes32 proofHash);
    event VerifierChanged(address indexed oldVerifier, address indexed newVerifier);

    function setUp() public {
        vm.warp(1_760_000_000);
        vm.prank(owner);
        escrow = new ProofPayEscrow(verifier, DISPUTE_WINDOW, REVIEW_GRACE, ARBITRATION_TIMEOUT);
        vm.deal(poster, 10 ether);
    }

    // ------------------------------------------------------------ helpers

    function _submitBy() internal view returns (uint64) {
        return uint64(block.timestamp) + 1 hours;
    }

    function _create() internal returns (uint256 id) {
        vm.prank(poster);
        id = escrow.createTask{ value: AMOUNT }(TITLE, DESCRIPTION, BEFORE, _submitBy());
    }

    function _createAccepted() internal returns (uint256 id) {
        id = _create();
        vm.prank(worker);
        escrow.acceptTask(id);
    }

    function _text(uint256 len) internal pure returns (string memory) {
        bytes memory b = new bytes(len);
        for (uint256 i = 0; i < len; i++) {
            b[i] = "a";
        }
        return string(b);
    }

    // ------------------------------------------------------------ constructor and admin (C-02, C-03, C-09, C-10)

    function test_constructor_setsConfigAndOwner() public view {
        assertEq(escrow.owner(), owner);
        assertEq(escrow.verifier(), verifier);
        assertEq(escrow.disputeWindow(), DISPUTE_WINDOW);
        assertEq(escrow.reviewGrace(), REVIEW_GRACE);
        assertEq(escrow.arbitrationTimeout(), ARBITRATION_TIMEOUT);
        assertEq(escrow.taskCount(), 0);
    }

    function test_constructor_rejectsZeroVerifier() public {
        vm.expectRevert(ProofPayEscrow.ZeroAddress.selector);
        new ProofPayEscrow(address(0), DISPUTE_WINDOW, REVIEW_GRACE, ARBITRATION_TIMEOUT);
    }

    function test_constructor_rejectsOutOfRangeTimes() public {
        uint64[2][3] memory bad =
            [[uint64(59), uint64(7 days + 1)], [uint64(299), uint64(7 days + 1)], [uint64(3599), uint64(30 days + 1)]];
        for (uint256 i = 0; i < 2; i++) {
            vm.expectRevert(ProofPayEscrow.InvalidConfig.selector);
            new ProofPayEscrow(verifier, bad[0][i], REVIEW_GRACE, ARBITRATION_TIMEOUT);
            vm.expectRevert(ProofPayEscrow.InvalidConfig.selector);
            new ProofPayEscrow(verifier, DISPUTE_WINDOW, bad[1][i], ARBITRATION_TIMEOUT);
            vm.expectRevert(ProofPayEscrow.InvalidConfig.selector);
            new ProofPayEscrow(verifier, DISPUTE_WINDOW, REVIEW_GRACE, bad[2][i]);
        }
    }

    function test_constructor_acceptsRangeEnds() public {
        new ProofPayEscrow(verifier, 60, 300, 3600);
        new ProofPayEscrow(verifier, 7 days, 7 days, 30 days);
    }

    function test_renounceOwnership_alwaysReverts() public {
        vm.prank(owner);
        vm.expectRevert(ProofPayEscrow.RenounceDisabled.selector);
        escrow.renounceOwnership();
        assertEq(escrow.owner(), owner);
    }

    function test_ownershipTransfer_needsAcceptOwnership() public {
        vm.prank(owner);
        escrow.transferOwnership(stranger);
        assertEq(escrow.owner(), owner);
        assertEq(escrow.pendingOwner(), stranger);

        vm.prank(worker);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, worker));
        escrow.acceptOwnership();

        vm.prank(stranger);
        escrow.acceptOwnership();
        assertEq(escrow.owner(), stranger);
    }

    function test_setVerifier_onlyOwner() public {
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, stranger));
        escrow.setVerifier(stranger);
    }

    function test_setVerifier_rejectsZero() public {
        vm.prank(owner);
        vm.expectRevert(ProofPayEscrow.ZeroAddress.selector);
        escrow.setVerifier(address(0));
    }

    function test_setVerifier_changesAndEmits() public {
        address next = makeAddr("next verifier");
        vm.expectEmit(true, true, false, false, address(escrow));
        emit VerifierChanged(verifier, next);
        vm.prank(owner);
        escrow.setVerifier(next);
        assertEq(escrow.verifier(), next);
    }

    // ------------------------------------------------------------ createTask (C-08, C-13, C-23)

    function test_createTask_storesTaskAndHoldsFunds() public {
        uint64 submitBy = _submitBy();
        vm.expectEmit(true, true, false, true, address(escrow));
        emit TaskCreated(1, poster, AMOUNT, BEFORE, submitBy, submitBy + REVIEW_GRACE);
        uint256 id = _create();

        assertEq(id, 1);
        assertEq(escrow.taskCount(), 1);
        assertEq(address(escrow).balance, AMOUNT);

        ProofPayEscrow.Task memory t = escrow.getTask(id);
        assertEq(t.poster, poster);
        assertEq(t.worker, address(0));
        assertEq(t.amount, AMOUNT);
        assertEq(t.beforeHash, BEFORE);
        assertEq(t.proofHash, bytes32(0));
        assertEq(t.createdAt, uint64(block.timestamp));
        assertEq(t.submitBy, submitBy);
        assertEq(t.reviewBy, submitBy + REVIEW_GRACE);
        assertEq(t.disputeUntil, 0);
        assertEq(t.attempts, 0);
        assertEq(t.score, 0);
        assertEq(uint8(t.status), uint8(ProofPayEscrow.Status.Open));
        assertEq(t.title, TITLE);
        assertEq(t.description, DESCRIPTION);
    }

    function test_createTask_idsStartAtOneAndIncrease() public {
        assertEq(_create(), 1);
        assertEq(_create(), 2);
        assertEq(_create(), 3);
        assertEq(escrow.taskCount(), 3);
    }

    function test_createTask_amountLimit() public {
        uint64 submitBy = _submitBy();
        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.AmountTooLow.selector);
        escrow.createTask{ value: 0.001 ether - 1 }(TITLE, DESCRIPTION, BEFORE, submitBy);

        vm.prank(poster);
        escrow.createTask{ value: 0.001 ether }(TITLE, DESCRIPTION, BEFORE, submitBy);
    }

    function test_createTask_rejectsZeroBeforeHash() public {
        uint64 submitBy = _submitBy();
        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.ZeroHash.selector);
        escrow.createTask{ value: AMOUNT }(TITLE, DESCRIPTION, bytes32(0), submitBy);
    }

    function test_createTask_deadlineLimits() public {
        uint64 nowTs = uint64(block.timestamp);
        vm.startPrank(poster);
        vm.expectRevert(ProofPayEscrow.InvalidDeadline.selector);
        escrow.createTask{ value: AMOUNT }(TITLE, DESCRIPTION, BEFORE, nowTs + 59);
        vm.expectRevert(ProofPayEscrow.InvalidDeadline.selector);
        escrow.createTask{ value: AMOUNT }(TITLE, DESCRIPTION, BEFORE, nowTs + 30 days + 1);
        escrow.createTask{ value: AMOUNT }(TITLE, DESCRIPTION, BEFORE, nowTs + 60);
        escrow.createTask{ value: AMOUNT }(TITLE, DESCRIPTION, BEFORE, nowTs + 30 days);
        vm.stopPrank();
    }

    function test_createTask_textLimits() public {
        uint64 submitBy = _submitBy();
        vm.startPrank(poster);
        vm.expectRevert(ProofPayEscrow.EmptyText.selector);
        escrow.createTask{ value: AMOUNT }("", DESCRIPTION, BEFORE, submitBy);
        vm.expectRevert(ProofPayEscrow.EmptyText.selector);
        escrow.createTask{ value: AMOUNT }(TITLE, "", BEFORE, submitBy);
        vm.expectRevert(ProofPayEscrow.TextTooLong.selector);
        escrow.createTask{ value: AMOUNT }(_text(81), DESCRIPTION, BEFORE, submitBy);
        vm.expectRevert(ProofPayEscrow.TextTooLong.selector);
        escrow.createTask{ value: AMOUNT }(TITLE, _text(501), BEFORE, submitBy);
        escrow.createTask{ value: AMOUNT }(_text(80), _text(500), BEFORE, submitBy);
        vm.stopPrank();
    }

    // ------------------------------------------------------------ acceptTask (C-14)

    function test_acceptTask_setsWorkerAndEmits() public {
        uint256 id = _create();
        vm.expectEmit(true, true, false, false, address(escrow));
        emit TaskAccepted(id, worker);
        vm.prank(worker);
        escrow.acceptTask(id);

        ProofPayEscrow.Task memory t = escrow.getTask(id);
        assertEq(t.worker, worker);
        assertEq(uint8(t.status), uint8(ProofPayEscrow.Status.Accepted));
    }

    function test_acceptTask_posterCannotAccept() public {
        uint256 id = _create();
        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.PosterCannotAccept.selector);
        escrow.acceptTask(id);
    }

    function test_acceptTask_onlyWhenOpen() public {
        uint256 id = _createAccepted();
        vm.prank(stranger);
        vm.expectRevert(
            abi.encodeWithSelector(
                ProofPayEscrow.WrongStatus.selector, ProofPayEscrow.Status.Open, ProofPayEscrow.Status.Accepted
            )
        );
        escrow.acceptTask(id);
    }

    function test_acceptTask_timeBoundary() public {
        uint256 a = _create();
        uint256 b = _create();
        uint64 submitBy = escrow.getTask(a).submitBy;

        vm.warp(submitBy - 1);
        vm.prank(worker);
        escrow.acceptTask(a); // before submitBy: works

        vm.warp(submitBy);
        vm.prank(worker);
        vm.expectRevert(ProofPayEscrow.SubmitClosed.selector);
        escrow.acceptTask(b); // at submitBy: reverts
    }

    // ------------------------------------------------------------ submitProof (C-15)

    function test_submitProof_storesProofAndEmits() public {
        uint256 id = _createAccepted();
        vm.expectEmit(true, false, false, true, address(escrow));
        emit ProofSubmitted(id, 1, PROOF);
        vm.prank(worker);
        escrow.submitProof(id, PROOF);

        ProofPayEscrow.Task memory t = escrow.getTask(id);
        assertEq(t.proofHash, PROOF);
        assertEq(t.attempts, 1);
        assertEq(uint8(t.status), uint8(ProofPayEscrow.Status.Submitted));
        assertEq(t.amount, AMOUNT);
    }

    function test_submitProof_onlyWorker() public {
        uint256 id = _createAccepted();
        address[2] memory wrong = [poster, stranger];
        for (uint256 i = 0; i < wrong.length; i++) {
            vm.prank(wrong[i]);
            vm.expectRevert(ProofPayEscrow.NotWorker.selector);
            escrow.submitProof(id, PROOF);
        }
    }

    function test_submitProof_openTaskHasNoWorker() public {
        uint256 id = _create();
        vm.prank(worker);
        vm.expectRevert(ProofPayEscrow.NotWorker.selector);
        escrow.submitProof(id, PROOF);
    }

    function test_submitProof_onlyWhenAccepted() public {
        uint256 id = _createAccepted();
        vm.startPrank(worker);
        escrow.submitProof(id, PROOF);
        vm.expectRevert(
            abi.encodeWithSelector(
                ProofPayEscrow.WrongStatus.selector, ProofPayEscrow.Status.Accepted, ProofPayEscrow.Status.Submitted
            )
        );
        escrow.submitProof(id, keccak256("another photo"));
        vm.stopPrank();
    }

    function test_submitProof_timeBoundary() public {
        uint256 a = _createAccepted();
        uint256 b = _createAccepted();
        uint64 submitBy = escrow.getTask(a).submitBy;

        vm.warp(submitBy - 1);
        vm.prank(worker);
        escrow.submitProof(a, PROOF); // before submitBy: works

        vm.warp(submitBy);
        vm.prank(worker);
        vm.expectRevert(ProofPayEscrow.SubmitClosed.selector);
        escrow.submitProof(b, PROOF); // at submitBy: reverts
    }

    function test_submitProof_rejectsZeroHash() public {
        uint256 id = _createAccepted();
        vm.prank(worker);
        vm.expectRevert(ProofPayEscrow.ZeroHash.selector);
        escrow.submitProof(id, bytes32(0));
    }

    function test_submitProof_rejectsBeforeHash() public {
        uint256 id = _createAccepted();
        vm.prank(worker);
        vm.expectRevert(ProofPayEscrow.SameAsBefore.selector);
        escrow.submitProof(id, BEFORE);
    }

    // ------------------------------------------------------------ taskExists (C-12)

    function test_taskExists_rejectsUnknownIds() public {
        _create();
        uint256[3] memory ids = [uint256(0), escrow.taskCount() + 1, type(uint256).max];
        for (uint256 i = 0; i < ids.length; i++) {
            vm.expectRevert(ProofPayEscrow.TaskNotFound.selector);
            escrow.getTask(ids[i]);

            vm.prank(worker);
            vm.expectRevert(ProofPayEscrow.TaskNotFound.selector);
            escrow.acceptTask(ids[i]);

            vm.prank(worker);
            vm.expectRevert(ProofPayEscrow.TaskNotFound.selector);
            escrow.submitProof(ids[i], PROOF);
        }
    }

    // ------------------------------------------------------------ fuzz

    function testFuzz_createTask_holdsExactValue(uint96 value, uint64 delay) public {
        value = uint96(bound(value, 0.001 ether, 5 ether));
        delay = uint64(bound(delay, 60, 30 days));
        uint64 submitBy = uint64(block.timestamp) + delay;
        vm.prank(poster);
        uint256 id = escrow.createTask{ value: value }(TITLE, DESCRIPTION, BEFORE, submitBy);
        assertEq(address(escrow).balance, value);
        assertEq(escrow.getTask(id).amount, value);
        assertEq(escrow.getTask(id).reviewBy, submitBy + REVIEW_GRACE);
    }
}
