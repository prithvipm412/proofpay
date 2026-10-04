// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import { Test } from "forge-std/Test.sol";
import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";
import { ProofPayEscrow } from "../contracts/ProofPayEscrow.sol";
import { RejectingReceiver } from "./mocks/RejectingReceiver.sol";
import { ReentrantReceiver } from "./mocks/ReentrantReceiver.sol";

/// @notice Unit tests C-T1 to C-T13 (section 9.6). C-T14 is in ProofPayInvariant.t.sol.
/// Each test name starts with the requirement ID it covers.
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
    uint256 internal constant POSTER_FUNDS = 10 ether;
    bytes32 internal constant BEFORE = keccak256("before photo");
    bytes32 internal constant PROOF = keccak256("after photo");
    bytes32 internal constant PROOF2 = keccak256("after photo 2");
    bytes32 internal constant PROOF3 = keccak256("after photo 3");
    string internal constant TITLE = "Remove the litter around this bench";
    string internal constant DESCRIPTION = "Pick up all litter on the grass around the bench.";
    string internal constant REASON = "AI check: task complete (confidence 86)";

    event TaskCreated(
        uint256 indexed id, address indexed poster, uint256 amount, bytes32 beforeHash, uint64 submitBy, uint64 reviewBy
    );
    event TaskAccepted(uint256 indexed id, address indexed worker);
    event ProofSubmitted(uint256 indexed id, uint8 attempt, bytes32 proofHash);
    event VerdictRecorded(uint256 indexed id, uint8 attempt, bytes32 proofHash, bool pass, uint8 score, string reason);
    event Disputed(uint256 indexed id);
    event DisputeResolved(uint256 indexed id, bool payWorker);
    event DisputeExpired(uint256 indexed id);
    event Released(uint256 indexed id, address indexed worker, uint256 amount);
    event Refunded(uint256 indexed id, address indexed poster, uint256 amount);
    event PaymentDeferred(address indexed to, uint256 amount);
    event Withdrawn(address indexed from, address indexed to, uint256 amount);
    event VerifierChanged(address indexed oldVerifier, address indexed newVerifier);
    event ReentryResult(bool ok);

    function setUp() public {
        vm.warp(1_760_000_000);
        vm.prank(owner);
        escrow = new ProofPayEscrow(verifier, DISPUTE_WINDOW, REVIEW_GRACE, ARBITRATION_TIMEOUT);
        vm.deal(poster, POSTER_FUNDS);
    }

    // ------------------------------------------------------------ helpers

    function _submitBy() internal view returns (uint64) {
        return uint64(block.timestamp) + 1 hours;
    }

    function _createAs(address who) internal returns (uint256 id) {
        vm.prank(who);
        id = escrow.createTask{ value: AMOUNT }(TITLE, DESCRIPTION, BEFORE, _submitBy());
    }

    function _create() internal returns (uint256) {
        return _createAs(poster);
    }

    function _acceptAs(uint256 id, address who) internal {
        vm.prank(who);
        escrow.acceptTask(id);
    }

    function _createAccepted() internal returns (uint256 id) {
        id = _create();
        _acceptAs(id, worker);
    }

    function _submit(uint256 id, bytes32 proofHash) internal {
        vm.prank(escrow.getTask(id).worker);
        escrow.submitProof(id, proofHash);
    }

    /// Records a verdict for the current attempt and proof hash.
    function _verdict(uint256 id, bool pass) internal {
        ProofPayEscrow.Task memory t = escrow.getTask(id);
        vm.prank(verifier);
        escrow.recordVerdict(id, t.attempts, t.proofHash, pass, pass ? 86 : 20, REASON);
    }

    function _approvedFor(address p, address w) internal returns (uint256 id) {
        id = _createAs(p);
        _acceptAs(id, w);
        _submit(id, PROOF);
        _verdict(id, true);
    }

    function _approved() internal returns (uint256) {
        return _approvedFor(poster, worker);
    }

    function _disputed() internal returns (uint256 id) {
        id = _approved();
        vm.prank(poster);
        escrow.dispute(id);
    }

    function _arbitrationEnd(uint256 id) internal view returns (uint256) {
        return uint256(escrow.getTask(id).disputeUntil) + ARBITRATION_TIMEOUT;
    }

    function _assertStatus(uint256 id, ProofPayEscrow.Status expected) internal view {
        assertEq(uint8(escrow.getTask(id).status), uint8(expected), "status");
    }

    function _expectWrongStatus(ProofPayEscrow.Status expected, ProofPayEscrow.Status actual) internal {
        vm.expectRevert(abi.encodeWithSelector(ProofPayEscrow.WrongStatus.selector, expected, actual));
    }

    function _expectNotOwner(address who) internal {
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, who));
    }

    function _text(uint256 len) internal pure returns (string memory) {
        bytes memory b = new bytes(len);
        for (uint256 i = 0; i < len; i++) {
            b[i] = "a";
        }
        return string(b);
    }

    // ------------------------------------------------------------ C-T1 normal path

    function test_CT1_normalPath_paysWorkerAndKeepsAmount() public {
        uint256 id = _create();
        assertEq(address(escrow).balance, AMOUNT);
        _acceptAs(id, worker);
        _submit(id, PROOF);

        vm.expectEmit(true, false, false, true, address(escrow));
        emit VerdictRecorded(id, 1, PROOF, true, 86, REASON);
        vm.prank(verifier);
        escrow.recordVerdict(id, 1, PROOF, true, 86, REASON);

        ProofPayEscrow.Task memory t = escrow.getTask(id);
        assertEq(uint8(t.status), uint8(ProofPayEscrow.Status.Approved));
        assertEq(t.score, 86);
        assertEq(t.disputeUntil, uint64(block.timestamp) + DISPUTE_WINDOW);

        vm.warp(t.disputeUntil);
        uint256 workerBefore = worker.balance;
        vm.expectEmit(true, true, false, true, address(escrow));
        emit Released(id, worker, AMOUNT);
        vm.prank(stranger); // C-20: any caller
        escrow.release(id);

        assertEq(worker.balance, workerBefore + AMOUNT);
        assertEq(address(escrow).balance, 0);
        assertEq(escrow.withdrawable(worker), 0);
        t = escrow.getTask(id);
        assertEq(uint8(t.status), uint8(ProofPayEscrow.Status.Paid));
        assertEq(t.amount, AMOUNT); // original amount is kept after payment
    }

    // ------------------------------------------------------------ C-T2 fail, new proof, pass

    function test_CT2_failThenNewProofThenPass() public {
        uint256 id = _createAccepted();
        _submit(id, PROOF);

        vm.prank(verifier);
        escrow.recordVerdict(id, 1, PROOF, false, 20, "AI check: task not complete");
        ProofPayEscrow.Task memory t = escrow.getTask(id);
        assertEq(uint8(t.status), uint8(ProofPayEscrow.Status.Accepted));
        assertEq(t.score, 20);
        assertEq(t.disputeUntil, 0);
        assertEq(t.worker, worker);

        vm.expectEmit(true, false, false, true, address(escrow));
        emit ProofSubmitted(id, 2, PROOF2);
        _submit(id, PROOF2);

        vm.prank(verifier);
        escrow.recordVerdict(id, 2, PROOF2, true, 90, REASON);
        t = escrow.getTask(id);
        assertEq(uint8(t.status), uint8(ProofPayEscrow.Status.Approved));
        assertEq(t.attempts, 2);
        assertEq(t.proofHash, PROOF2);
        assertEq(t.score, 90);

        vm.warp(t.disputeUntil);
        uint256 workerBefore = worker.balance;
        escrow.release(id);
        assertEq(worker.balance, workerBefore + AMOUNT);
    }

    // ------------------------------------------------------------ C-T3 attempt limit

    function test_CT3_fourthSubmitRevertsAndPosterRefundsAtOnce() public {
        uint256 id = _createAccepted();
        bytes32[3] memory proofs = [PROOF, PROOF2, PROOF3];
        for (uint256 i = 0; i < 3; i++) {
            if (i == 2) {
                // After two fails the poster still cannot refund before submitBy.
                vm.prank(poster);
                vm.expectRevert(ProofPayEscrow.RefundNotAvailable.selector);
                escrow.refund(id);
            }
            _submit(id, proofs[i]);
            _verdict(id, false);
        }
        assertEq(escrow.getTask(id).attempts, 3);
        _assertStatus(id, ProofPayEscrow.Status.Accepted);

        vm.prank(worker);
        vm.expectRevert(ProofPayEscrow.TooManyAttempts.selector);
        escrow.submitProof(id, keccak256("after photo 4"));

        // Still before submitBy: refund works at once because attempts == MAX_ATTEMPTS.
        assertLt(block.timestamp, escrow.getTask(id).submitBy);
        vm.expectEmit(true, true, false, true, address(escrow));
        emit Refunded(id, poster, AMOUNT);
        vm.prank(poster);
        escrow.refund(id);
        assertEq(poster.balance, POSTER_FUNDS);
        assertEq(address(escrow).balance, 0);
        _assertStatus(id, ProofPayEscrow.Status.Refunded);
        assertEq(escrow.getTask(id).amount, AMOUNT);
    }

    // ------------------------------------------------------------ C-T4 stale verdict

    function test_CT4_oldAttemptVerdictIsStale() public {
        uint256 id = _createAccepted();
        _submit(id, PROOF);
        _verdict(id, false);
        _submit(id, PROOF2);

        vm.startPrank(verifier);
        vm.expectRevert(ProofPayEscrow.StaleVerdict.selector);
        escrow.recordVerdict(id, 1, PROOF, true, 99, REASON); // attempt 1 after attempt 2
        vm.expectRevert(ProofPayEscrow.StaleVerdict.selector);
        escrow.recordVerdict(id, 1, PROOF2, true, 99, REASON); // right hash, old attempt
        vm.expectRevert(ProofPayEscrow.StaleVerdict.selector);
        escrow.recordVerdict(id, 3, PROOF2, true, 99, REASON); // future attempt
        vm.expectRevert(ProofPayEscrow.StaleVerdict.selector);
        escrow.recordVerdict(id, 2, PROOF, true, 99, REASON); // wrong proof hash
        escrow.recordVerdict(id, 2, PROOF2, true, 99, REASON);
        vm.stopPrank();
        _assertStatus(id, ProofPayEscrow.Status.Approved);
    }

    function test_CT4_secondVerdictForSameAttemptRevertsWrongStatus() public {
        uint256 id = _createAccepted();
        _submit(id, PROOF);
        _verdict(id, true);
        vm.prank(verifier);
        _expectWrongStatus(ProofPayEscrow.Status.Submitted, ProofPayEscrow.Status.Approved);
        escrow.recordVerdict(id, 1, PROOF, false, 0, "");
    }

    // ------------------------------------------------------------ C-T5 time boundaries

    function test_CT5_acceptBoundary() public {
        uint256 a = _create();
        uint256 b = _create();
        uint64 submitBy = escrow.getTask(a).submitBy;

        vm.warp(submitBy - 1);
        _acceptAs(a, worker); // before submitBy: works

        vm.warp(submitBy);
        vm.prank(worker);
        vm.expectRevert(ProofPayEscrow.SubmitClosed.selector);
        escrow.acceptTask(b); // at submitBy: reverts
    }

    function test_CT5_submitBoundary() public {
        uint256 a = _createAccepted();
        uint256 b = _createAccepted();
        uint64 submitBy = escrow.getTask(a).submitBy;

        vm.warp(submitBy - 1);
        _submit(a, PROOF); // before submitBy: works

        vm.warp(submitBy);
        vm.prank(worker);
        vm.expectRevert(ProofPayEscrow.SubmitClosed.selector);
        escrow.submitProof(b, PROOF); // at submitBy: reverts
    }

    function test_CT5_verdictBoundary() public {
        uint256 a = _createAccepted();
        uint256 b = _createAccepted();
        _submit(a, PROOF);
        _submit(b, PROOF);
        uint64 reviewBy = escrow.getTask(a).reviewBy;

        vm.warp(reviewBy - 1);
        _verdict(a, true); // before reviewBy: works

        vm.warp(reviewBy);
        vm.prank(verifier);
        vm.expectRevert(ProofPayEscrow.ReviewClosed.selector);
        escrow.recordVerdict(b, 1, PROOF, true, 86, REASON); // at reviewBy: reverts
    }

    function test_CT5_refundSubmittedBoundary() public {
        uint256 id = _createAccepted();
        _submit(id, PROOF);
        uint64 reviewBy = escrow.getTask(id).reviewBy;

        vm.warp(reviewBy - 1);
        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.RefundNotAvailable.selector);
        escrow.refund(id);

        vm.warp(reviewBy);
        vm.prank(poster);
        escrow.refund(id);
        _assertStatus(id, ProofPayEscrow.Status.Refunded);
        assertEq(poster.balance, POSTER_FUNDS);
    }

    function test_CT5_refundAcceptedBoundary() public {
        uint256 id = _createAccepted();
        uint64 submitBy = escrow.getTask(id).submitBy;

        vm.warp(submitBy - 1);
        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.RefundNotAvailable.selector);
        escrow.refund(id);

        vm.warp(submitBy);
        vm.prank(poster);
        escrow.refund(id);
        _assertStatus(id, ProofPayEscrow.Status.Refunded);
    }

    function test_CT5_disputeAndReleaseBoundary() public {
        uint256 a = _approved();
        uint256 b = _approved();
        uint64 disputeUntil = escrow.getTask(a).disputeUntil;
        assertEq(escrow.getTask(b).disputeUntil, disputeUntil);

        vm.warp(disputeUntil - 1);
        vm.expectRevert(ProofPayEscrow.DisputeWindowOpen.selector);
        escrow.release(a); // before disputeUntil: release reverts
        vm.prank(poster);
        escrow.dispute(a); // before disputeUntil: dispute works

        vm.warp(disputeUntil);
        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.DisputeWindowClosed.selector);
        escrow.dispute(b); // at disputeUntil: dispute reverts
        escrow.release(b); // at disputeUntil: release works
        _assertStatus(b, ProofPayEscrow.Status.Paid);
    }

    // Arbitration boundary T = disputeUntil + arbitrationTimeout. Both call orders at T - 1 and at T.

    function test_CT5_arbitration_beforeT_resolveThenExpire() public {
        uint256 id = _disputed();
        vm.warp(_arbitrationEnd(id) - 1);

        vm.prank(owner);
        escrow.resolveDispute(id, true); // works
        _assertStatus(id, ProofPayEscrow.Status.Paid);

        _expectWrongStatus(ProofPayEscrow.Status.Disputed, ProofPayEscrow.Status.Paid);
        escrow.expireDispute(id);
    }

    function test_CT5_arbitration_beforeT_expireThenResolve() public {
        uint256 id = _disputed();
        vm.warp(_arbitrationEnd(id) - 1);

        vm.expectRevert(ProofPayEscrow.ArbitrationNotExpired.selector);
        escrow.expireDispute(id);
        _assertStatus(id, ProofPayEscrow.Status.Disputed);

        vm.prank(owner);
        escrow.resolveDispute(id, false); // still works
        _assertStatus(id, ProofPayEscrow.Status.Refunded);
    }

    function test_CT5_arbitration_atT_resolveThenExpire() public {
        uint256 id = _disputed();
        vm.warp(_arbitrationEnd(id));

        vm.startPrank(owner);
        vm.expectRevert(ProofPayEscrow.ArbitrationClosed.selector);
        escrow.resolveDispute(id, true);
        vm.expectRevert(ProofPayEscrow.ArbitrationClosed.selector);
        escrow.resolveDispute(id, false);
        vm.stopPrank();
        _assertStatus(id, ProofPayEscrow.Status.Disputed);

        escrow.expireDispute(id); // works
        _assertStatus(id, ProofPayEscrow.Status.Refunded);
        assertEq(poster.balance, POSTER_FUNDS);
    }

    function test_CT5_arbitration_atT_expireThenResolve() public {
        uint256 id = _disputed();
        vm.warp(_arbitrationEnd(id));

        escrow.expireDispute(id); // works
        _assertStatus(id, ProofPayEscrow.Status.Refunded);

        vm.prank(owner);
        _expectWrongStatus(ProofPayEscrow.Status.Disputed, ProofPayEscrow.Status.Refunded);
        escrow.resolveDispute(id, true);
    }

    function test_CT5_arbitration_afterT_resolveStillClosed() public {
        uint256 id = _disputed();
        vm.warp(_arbitrationEnd(id) + 1 days);
        vm.prank(owner);
        vm.expectRevert(ProofPayEscrow.ArbitrationClosed.selector);
        escrow.resolveDispute(id, true);
    }

    /// At any time after the dispute, exactly one of resolveDispute and expireDispute works (C-18, C-19).
    function testFuzz_CT5_arbitrationExactlyOnePathOpen(uint64 offset, bool payWorker) public {
        uint256 id = _disputed();
        uint256 end = _arbitrationEnd(id);
        uint256 disputedAt = block.timestamp;
        // Half of the runs land within 2 seconds of T, so that the exact boundary is always exercised.
        uint256 at = offset % 2 == 0
            ? end - 2 + (uint256(offset / 2) % 5)
            : bound(uint256(offset), disputedAt, end + 2 * ARBITRATION_TIMEOUT);
        vm.warp(at);

        uint256 snap = vm.snapshotState();
        vm.prank(owner);
        (bool resolveOk,) = address(escrow).call(abi.encodeCall(escrow.resolveDispute, (id, payWorker)));
        vm.revertToState(snap);
        (bool expireOk,) = address(escrow).call(abi.encodeCall(escrow.expireDispute, (id)));

        assertTrue(resolveOk != expireOk, "exactly one path");
        assertEq(resolveOk, at < end, "resolve only before T");
    }

    // ------------------------------------------------------------ C-T6 dispute outcomes

    function test_CT6_disputeThenResolvePayWorker() public {
        uint256 id = _approved();
        vm.expectEmit(true, false, false, false, address(escrow));
        emit Disputed(id);
        vm.prank(poster);
        escrow.dispute(id);
        _assertStatus(id, ProofPayEscrow.Status.Disputed);

        uint256 workerBefore = worker.balance;
        vm.expectEmit(true, false, false, true, address(escrow));
        emit DisputeResolved(id, true);
        vm.expectEmit(true, true, false, true, address(escrow));
        emit Released(id, worker, AMOUNT);
        vm.prank(owner);
        escrow.resolveDispute(id, true);

        _assertStatus(id, ProofPayEscrow.Status.Paid);
        assertEq(worker.balance, workerBefore + AMOUNT);
        assertEq(address(escrow).balance, 0);
    }

    function test_CT6_disputeThenResolveRefundPoster() public {
        uint256 id = _disputed();
        vm.expectEmit(true, false, false, true, address(escrow));
        emit DisputeResolved(id, false);
        vm.expectEmit(true, true, false, true, address(escrow));
        emit Refunded(id, poster, AMOUNT);
        vm.prank(owner);
        escrow.resolveDispute(id, false);

        _assertStatus(id, ProofPayEscrow.Status.Refunded);
        assertEq(poster.balance, POSTER_FUNDS);
        assertEq(address(escrow).balance, 0);
        assertEq(escrow.getTask(id).amount, AMOUNT);
    }

    function test_CT6_disputeThenExpireAfterTimeout() public {
        uint256 id = _disputed();
        vm.warp(_arbitrationEnd(id));
        vm.expectEmit(true, false, false, false, address(escrow));
        emit DisputeExpired(id);
        vm.expectEmit(true, true, false, true, address(escrow));
        emit Refunded(id, poster, AMOUNT);
        vm.prank(stranger); // C-19: any caller
        escrow.expireDispute(id);

        _assertStatus(id, ProofPayEscrow.Status.Refunded);
        assertEq(poster.balance, POSTER_FUNDS);
        assertEq(address(escrow).balance, 0);
    }

    // Both call orders at the arbitration boundary, for each resolve outcome (C-T6 with C-T5 timing).

    function test_CT6_boundaryOrders_bothOutcomes() public {
        for (uint256 i = 0; i < 2; i++) {
            bool payWorker = i == 0;

            // T - 1: resolve wins, a later expire reverts.
            uint256 a = _disputed();
            vm.warp(_arbitrationEnd(a) - 1);
            vm.prank(owner);
            escrow.resolveDispute(a, payWorker);
            ProofPayEscrow.Status done = payWorker ? ProofPayEscrow.Status.Paid : ProofPayEscrow.Status.Refunded;
            _assertStatus(a, done);
            _expectWrongStatus(ProofPayEscrow.Status.Disputed, done);
            escrow.expireDispute(a);

            // T: resolve reverts ArbitrationClosed, expire works, a later resolve reverts.
            uint256 b = _disputed();
            vm.warp(_arbitrationEnd(b));
            vm.prank(owner);
            vm.expectRevert(ProofPayEscrow.ArbitrationClosed.selector);
            escrow.resolveDispute(b, payWorker);
            escrow.expireDispute(b);
            _assertStatus(b, ProofPayEscrow.Status.Refunded);
            vm.prank(owner);
            _expectWrongStatus(ProofPayEscrow.Status.Disputed, ProofPayEscrow.Status.Refunded);
            escrow.resolveDispute(b, payWorker);
        }
        assertEq(address(escrow).balance, 0);
    }

    // ------------------------------------------------------------ C-T7 double release and final states

    function test_CT7_doubleReleaseReverts() public {
        uint256 id = _approved();
        vm.warp(escrow.getTask(id).disputeUntil);
        escrow.release(id);
        _expectWrongStatus(ProofPayEscrow.Status.Approved, ProofPayEscrow.Status.Paid);
        escrow.release(id);
    }

    function test_CT7_finalStatesRejectFurtherPayouts() public {
        uint256 id = _approved();
        vm.warp(escrow.getTask(id).disputeUntil);
        escrow.release(id);

        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.RefundNotAvailable.selector);
        escrow.refund(id);
        vm.prank(poster);
        _expectWrongStatus(ProofPayEscrow.Status.Approved, ProofPayEscrow.Status.Paid);
        escrow.dispute(id);

        uint256 r = _create();
        vm.prank(poster);
        escrow.refund(r);
        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.RefundNotAvailable.selector);
        escrow.refund(r);
        vm.prank(worker);
        _expectWrongStatus(ProofPayEscrow.Status.Open, ProofPayEscrow.Status.Refunded);
        escrow.acceptTask(r);
    }

    function test_CT7_refundNotAvailableWhileApprovedOrDisputed() public {
        uint256 id = _approved();
        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.RefundNotAvailable.selector);
        escrow.refund(id);
        vm.prank(poster);
        escrow.dispute(id);
        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.RefundNotAvailable.selector);
        escrow.refund(id);
    }

    function test_CT7_openRefundWorksAtAnyTime() public {
        uint256 id = _create();
        vm.prank(poster);
        escrow.refund(id);
        _assertStatus(id, ProofPayEscrow.Status.Refunded);
        assertEq(poster.balance, POSTER_FUNDS);
    }

    // ------------------------------------------------------------ C-T8 rejecting receiver

    function test_CT8_rejectingWorker_paymentDeferredThenWithdrawnElsewhere() public {
        address rr = address(new RejectingReceiver());
        uint256 id = _approvedFor(poster, rr);
        vm.warp(escrow.getTask(id).disputeUntil);

        vm.expectEmit(true, false, false, true, address(escrow));
        emit PaymentDeferred(rr, AMOUNT);
        escrow.release(id); // does not revert

        _assertStatus(id, ProofPayEscrow.Status.Paid);
        assertEq(escrow.withdrawable(rr), AMOUNT);
        assertEq(address(escrow).balance, AMOUNT);
        assertEq(rr.balance, 0);

        // Sending to itself still fails: the whole withdraw reverts and the credit stays.
        vm.prank(rr);
        vm.expectRevert(ProofPayEscrow.TransferFailed.selector);
        escrow.withdraw(payable(rr));
        assertEq(escrow.withdrawable(rr), AMOUNT);

        // Other accounts cannot take it.
        vm.prank(stranger);
        vm.expectRevert(ProofPayEscrow.NothingToWithdraw.selector);
        escrow.withdraw(payable(stranger));

        address payable sink = payable(makeAddr("sink"));
        vm.expectEmit(true, true, false, true, address(escrow));
        emit Withdrawn(rr, sink, AMOUNT);
        vm.prank(rr);
        escrow.withdraw(sink);
        assertEq(sink.balance, AMOUNT);
        assertEq(escrow.withdrawable(rr), 0);
        assertEq(address(escrow).balance, 0);

        vm.prank(rr);
        vm.expectRevert(ProofPayEscrow.NothingToWithdraw.selector);
        escrow.withdraw(sink);
    }

    function test_CT8_rejectingPoster_refundDeferred() public {
        address rr = address(new RejectingReceiver());
        vm.deal(rr, 1 ether);
        uint256 id = _createAs(rr);
        vm.prank(rr);
        escrow.refund(id);
        _assertStatus(id, ProofPayEscrow.Status.Refunded);
        assertEq(escrow.withdrawable(rr), AMOUNT);
        assertEq(address(escrow).balance, AMOUNT);
    }

    function test_CT8_withdrawRejectsZeroAddress() public {
        address rr = address(new RejectingReceiver());
        vm.deal(rr, 1 ether);
        uint256 id = _createAs(rr);
        vm.prank(rr);
        escrow.refund(id);
        vm.prank(rr);
        vm.expectRevert(ProofPayEscrow.ZeroAddress.selector);
        escrow.withdraw(payable(address(0)));
    }

    // ------------------------------------------------------------ C-T9 reentrant receiver

    function test_CT9_reentrantPoster_refundOnce() public {
        ReentrantReceiver rr = new ReentrantReceiver();
        vm.deal(address(rr), 1 ether);
        uint256 id = _createAs(address(rr));
        uint256 other = _create(); // MON of another poster is also in the contract
        rr.setReentry(address(escrow), abi.encodeCall(escrow.refund, (id)));

        vm.expectEmit(false, false, false, true, address(rr));
        emit ReentryResult(false);
        vm.prank(address(rr));
        escrow.refund(id);

        assertEq(rr.reentryAttempts(), 1);
        assertEq(address(rr).balance, 1 ether); // got back exactly AMOUNT
        assertEq(escrow.withdrawable(address(rr)), 0);
        assertEq(address(escrow).balance, AMOUNT);
        _assertStatus(other, ProofPayEscrow.Status.Open);
    }

    function test_CT9_reentrantWorker_releaseOnce() public {
        ReentrantReceiver rr = new ReentrantReceiver();
        uint256 id = _approvedFor(poster, address(rr));
        _create();
        rr.setReentry(address(escrow), abi.encodeCall(escrow.release, (id)));
        vm.warp(escrow.getTask(id).disputeUntil);

        vm.expectEmit(false, false, false, true, address(rr));
        emit ReentryResult(false);
        escrow.release(id);

        assertEq(address(rr).balance, AMOUNT);
        assertEq(escrow.withdrawable(address(rr)), 0);
        assertEq(address(escrow).balance, AMOUNT);
    }

    function test_CT9_reentrantPoster_expireDisputeOnce() public {
        ReentrantReceiver rr = new ReentrantReceiver();
        vm.deal(address(rr), 1 ether);
        uint256 id = _approvedFor(address(rr), worker);
        _create();
        vm.prank(address(rr));
        escrow.dispute(id);
        rr.setReentry(address(escrow), abi.encodeCall(escrow.expireDispute, (id)));
        vm.warp(_arbitrationEnd(id));

        vm.expectEmit(false, false, false, true, address(rr));
        emit ReentryResult(false);
        escrow.expireDispute(id);

        assertEq(address(rr).balance, 1 ether);
        assertEq(address(escrow).balance, AMOUNT);
    }

    function test_CT9_reentrantWithdraw_once() public {
        ReentrantReceiver rr = new ReentrantReceiver();
        uint256 id = _approvedFor(poster, address(rr));
        _create();
        rr.setRejectPayments(true);
        vm.warp(escrow.getTask(id).disputeUntil);
        escrow.release(id);
        assertEq(escrow.withdrawable(address(rr)), AMOUNT);

        rr.setRejectPayments(false);
        rr.setReentry(address(escrow), abi.encodeCall(escrow.withdraw, (payable(address(rr)))));
        vm.expectEmit(false, false, false, true, address(rr));
        emit ReentryResult(false);
        vm.prank(address(rr));
        escrow.withdraw(payable(address(rr)));

        assertEq(address(rr).balance, AMOUNT);
        assertEq(escrow.withdrawable(address(rr)), 0);
        assertEq(address(escrow).balance, AMOUNT);
    }

    // ------------------------------------------------------------ C-T10 authorization

    function test_CT10_acceptTask_posterRejected() public {
        uint256 id = _create();
        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.PosterCannotAccept.selector);
        escrow.acceptTask(id);
    }

    function test_CT10_submitProof_onlyWorker() public {
        uint256 id = _createAccepted();
        address[4] memory wrong = [poster, stranger, verifier, owner];
        for (uint256 i = 0; i < wrong.length; i++) {
            vm.prank(wrong[i]);
            vm.expectRevert(ProofPayEscrow.NotWorker.selector);
            escrow.submitProof(id, PROOF);
        }
        uint256 open = _create(); // an Open task has no worker
        vm.prank(worker);
        vm.expectRevert(ProofPayEscrow.NotWorker.selector);
        escrow.submitProof(open, PROOF);
    }

    function test_CT10_recordVerdict_onlyVerifier() public {
        uint256 id = _createAccepted();
        _submit(id, PROOF);
        address[4] memory wrong = [poster, worker, owner, stranger];
        for (uint256 i = 0; i < wrong.length; i++) {
            vm.prank(wrong[i]);
            vm.expectRevert(ProofPayEscrow.NotVerifier.selector);
            escrow.recordVerdict(id, 1, PROOF, true, 86, REASON);
        }
    }

    function test_CT10_dispute_onlyPoster() public {
        uint256 id = _approved();
        address[4] memory wrong = [worker, owner, verifier, stranger];
        for (uint256 i = 0; i < wrong.length; i++) {
            vm.prank(wrong[i]);
            vm.expectRevert(ProofPayEscrow.NotPoster.selector);
            escrow.dispute(id);
        }
    }

    function test_CT10_refund_onlyPoster() public {
        uint256 id = _create();
        address[4] memory wrong = [worker, owner, verifier, stranger];
        for (uint256 i = 0; i < wrong.length; i++) {
            vm.prank(wrong[i]);
            vm.expectRevert(ProofPayEscrow.NotPoster.selector);
            escrow.refund(id);
        }
    }

    function test_CT10_resolveDispute_onlyOwner() public {
        uint256 id = _disputed();
        address[4] memory wrong = [poster, worker, verifier, stranger];
        for (uint256 i = 0; i < wrong.length; i++) {
            vm.prank(wrong[i]);
            _expectNotOwner(wrong[i]);
            escrow.resolveDispute(id, true);
        }
    }

    function test_CT10_setVerifier_onlyOwner() public {
        address[4] memory wrong = [poster, worker, verifier, stranger];
        for (uint256 i = 0; i < wrong.length; i++) {
            vm.prank(wrong[i]);
            _expectNotOwner(wrong[i]);
            escrow.setVerifier(stranger);
        }
    }

    function test_CT10_unrestrictedFunctions(address anyone) public {
        vm.assume(anyone != address(0) && anyone.code.length == 0 && uint160(anyone) > 0x10);
        vm.assume(anyone != poster && anyone != worker && anyone != address(escrow));
        assumeNotPrecompile(anyone);
        assumeNotForgeAddress(anyone);

        // createTask
        vm.deal(anyone, 1 ether);
        uint256 own = _createAs(anyone);
        _assertStatus(own, ProofPayEscrow.Status.Open);

        // release
        uint256 a = _approved();
        vm.warp(escrow.getTask(a).disputeUntil);
        vm.prank(anyone);
        escrow.release(a);
        _assertStatus(a, ProofPayEscrow.Status.Paid);

        // expireDispute
        uint256 d = _disputed();
        vm.warp(_arbitrationEnd(d));
        vm.prank(anyone);
        escrow.expireDispute(d);
        _assertStatus(d, ProofPayEscrow.Status.Refunded);
    }

    function test_CT10_withdraw_anyEntitledAccount() public {
        address rr = address(new RejectingReceiver());
        vm.deal(rr, 1 ether);
        uint256 id = _createAs(rr);
        vm.prank(rr);
        escrow.refund(id);
        vm.prank(rr);
        escrow.withdraw(payable(stranger));
        assertEq(stranger.balance, AMOUNT);
    }

    // ------------------------------------------------------------ C-T11 input limits

    function test_CT11_createTask_amountLimit() public {
        uint64 submitBy = _submitBy();
        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.AmountTooLow.selector);
        escrow.createTask{ value: 0.001 ether - 1 }(TITLE, DESCRIPTION, BEFORE, submitBy);

        vm.prank(poster);
        escrow.createTask{ value: 0.001 ether }(TITLE, DESCRIPTION, BEFORE, submitBy);
    }

    function test_CT11_createTask_rejectsZeroBeforeHash() public {
        uint64 submitBy = _submitBy();
        vm.prank(poster);
        vm.expectRevert(ProofPayEscrow.ZeroHash.selector);
        escrow.createTask{ value: AMOUNT }(TITLE, DESCRIPTION, bytes32(0), submitBy);
    }

    function test_CT11_createTask_deadlineLimits() public {
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

    function test_CT11_createTask_textLimits() public {
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

    function test_CT11_submitProof_rejectsZeroHash() public {
        uint256 id = _createAccepted();
        vm.prank(worker);
        vm.expectRevert(ProofPayEscrow.ZeroHash.selector);
        escrow.submitProof(id, bytes32(0));
    }

    function test_CT11_submitProof_rejectsBeforeHash() public {
        uint256 id = _createAccepted();
        vm.prank(worker);
        vm.expectRevert(ProofPayEscrow.SameAsBefore.selector);
        escrow.submitProof(id, BEFORE);
    }

    function test_CT11_recordVerdict_scoreLimit() public {
        uint256 id = _createAccepted();
        _submit(id, PROOF);
        vm.startPrank(verifier);
        vm.expectRevert(ProofPayEscrow.ScoreTooHigh.selector);
        escrow.recordVerdict(id, 1, PROOF, true, 101, REASON);
        escrow.recordVerdict(id, 1, PROOF, true, 100, REASON);
        vm.stopPrank();
        assertEq(escrow.getTask(id).score, 100);
    }

    function test_CT11_recordVerdict_reasonLimit() public {
        uint256 id = _createAccepted();
        _submit(id, PROOF);
        vm.startPrank(verifier);
        vm.expectRevert(ProofPayEscrow.TextTooLong.selector);
        escrow.recordVerdict(id, 1, PROOF, false, 0, _text(121));
        escrow.recordVerdict(id, 1, PROOF, false, 0, _text(120));
        vm.stopPrank();
    }

    // ------------------------------------------------------------ C-T12 unknown task IDs

    function test_CT12_unknownIdsRevertOnEveryFunction() public {
        _create();
        uint256[3] memory ids = [uint256(0), escrow.taskCount() + 1, type(uint256).max];
        for (uint256 i = 0; i < ids.length; i++) {
            uint256 id = ids[i];

            vm.prank(worker);
            vm.expectRevert(ProofPayEscrow.TaskNotFound.selector);
            escrow.acceptTask(id);

            vm.prank(worker);
            vm.expectRevert(ProofPayEscrow.TaskNotFound.selector);
            escrow.submitProof(id, PROOF);

            vm.prank(verifier);
            vm.expectRevert(ProofPayEscrow.TaskNotFound.selector);
            escrow.recordVerdict(id, 1, PROOF, true, 86, REASON);

            vm.prank(poster);
            vm.expectRevert(ProofPayEscrow.TaskNotFound.selector);
            escrow.dispute(id);

            vm.prank(owner);
            vm.expectRevert(ProofPayEscrow.TaskNotFound.selector);
            escrow.resolveDispute(id, true);

            vm.expectRevert(ProofPayEscrow.TaskNotFound.selector);
            escrow.expireDispute(id);

            vm.expectRevert(ProofPayEscrow.TaskNotFound.selector);
            escrow.release(id);

            vm.prank(poster);
            vm.expectRevert(ProofPayEscrow.TaskNotFound.selector);
            escrow.refund(id);

            vm.expectRevert(ProofPayEscrow.TaskNotFound.selector);
            escrow.getTask(id);
        }
    }

    // ------------------------------------------------------------ C-T13 constructor and admin

    function test_CT13_constructor_setsConfigAndOwner() public view {
        assertEq(escrow.owner(), owner);
        assertEq(escrow.verifier(), verifier);
        assertEq(escrow.disputeWindow(), DISPUTE_WINDOW);
        assertEq(escrow.reviewGrace(), REVIEW_GRACE);
        assertEq(escrow.arbitrationTimeout(), ARBITRATION_TIMEOUT);
        assertEq(escrow.taskCount(), 0);
    }

    function test_CT13_constructor_rejectsZeroVerifier() public {
        vm.expectRevert(ProofPayEscrow.ZeroAddress.selector);
        new ProofPayEscrow(address(0), DISPUTE_WINDOW, REVIEW_GRACE, ARBITRATION_TIMEOUT);
    }

    function test_CT13_constructor_rejectsOutOfRangeTimes() public {
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

    function test_CT13_constructor_acceptsRangeEnds() public {
        new ProofPayEscrow(verifier, 60, 300, 3600);
        new ProofPayEscrow(verifier, 7 days, 7 days, 30 days);
    }

    function test_CT13_setVerifier_rejectsZero() public {
        vm.prank(owner);
        vm.expectRevert(ProofPayEscrow.ZeroAddress.selector);
        escrow.setVerifier(address(0));
    }

    function test_CT13_setVerifier_oldVerifierLosesAccess() public {
        address next = makeAddr("next verifier");
        uint256 id = _createAccepted();
        _submit(id, PROOF);

        vm.expectEmit(true, true, false, false, address(escrow));
        emit VerifierChanged(verifier, next);
        vm.prank(owner);
        escrow.setVerifier(next);
        assertEq(escrow.verifier(), next);

        vm.prank(verifier);
        vm.expectRevert(ProofPayEscrow.NotVerifier.selector);
        escrow.recordVerdict(id, 1, PROOF, true, 86, REASON);

        vm.prank(next);
        escrow.recordVerdict(id, 1, PROOF, true, 86, REASON);
        _assertStatus(id, ProofPayEscrow.Status.Approved);
    }

    function test_CT13_renounceOwnership_alwaysReverts() public {
        vm.prank(owner);
        vm.expectRevert(ProofPayEscrow.RenounceDisabled.selector);
        escrow.renounceOwnership();
        assertEq(escrow.owner(), owner);
    }

    function test_CT13_ownershipTransfer_needsAcceptOwnership() public {
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

    // ------------------------------------------------------------ C-13 to C-15 details (M1)

    function test_C13_createTask_storesTaskAndHoldsFunds() public {
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

    function test_C08_idsStartAtOneAndIncrease() public {
        assertEq(_create(), 1);
        assertEq(_create(), 2);
        assertEq(_create(), 3);
        assertEq(escrow.taskCount(), 3);
    }

    function test_C14_acceptTask_setsWorkerAndEmits() public {
        uint256 id = _create();
        vm.expectEmit(true, true, false, false, address(escrow));
        emit TaskAccepted(id, worker);
        _acceptAs(id, worker);

        ProofPayEscrow.Task memory t = escrow.getTask(id);
        assertEq(t.worker, worker);
        assertEq(uint8(t.status), uint8(ProofPayEscrow.Status.Accepted));
    }

    function test_C14_acceptTask_onlyWhenOpen() public {
        uint256 id = _createAccepted();
        vm.prank(stranger);
        _expectWrongStatus(ProofPayEscrow.Status.Open, ProofPayEscrow.Status.Accepted);
        escrow.acceptTask(id);
    }

    function test_C15_submitProof_storesProofAndEmits() public {
        uint256 id = _createAccepted();
        vm.expectEmit(true, false, false, true, address(escrow));
        emit ProofSubmitted(id, 1, PROOF);
        _submit(id, PROOF);

        ProofPayEscrow.Task memory t = escrow.getTask(id);
        assertEq(t.proofHash, PROOF);
        assertEq(t.attempts, 1);
        assertEq(uint8(t.status), uint8(ProofPayEscrow.Status.Submitted));
        assertEq(t.amount, AMOUNT);
    }

    function test_C15_submitProof_onlyWhenAccepted() public {
        uint256 id = _createAccepted();
        _submit(id, PROOF);
        vm.prank(worker);
        _expectWrongStatus(ProofPayEscrow.Status.Accepted, ProofPayEscrow.Status.Submitted);
        escrow.submitProof(id, PROOF2);
    }

    function testFuzz_C13_createTask_holdsExactValue(uint96 value, uint64 delay) public {
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
