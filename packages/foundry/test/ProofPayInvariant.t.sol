// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import { Test } from "forge-std/Test.sol";
import { ProofPayEscrow } from "../contracts/ProofPayEscrow.sol";
import { RejectingReceiver } from "./mocks/RejectingReceiver.sol";

/// @notice C-T14 handler: random actions on many tasks, with time jumps.
/// Each action uses the caller that the contract expects, so that most calls succeed.
/// A call that the contract rejects (for example a time rule) is ignored with try/catch.
/// The handler never sends unsolicited MON to the escrow.
contract EscrowHandler is Test {
    ProofPayEscrow public escrow;
    address public owner;
    address public verifier;
    address[] public actors;
    address payable public sink = payable(makeAddr("sink"));

    uint256 internal constant MAX_TASKS = 30;

    // Ghost counters of successful calls, to show that the run reached every function.
    mapping(bytes32 => uint256) public calls;

    constructor(ProofPayEscrow escrow_, address owner_, address verifier_) {
        escrow = escrow_;
        owner = owner_;
        verifier = verifier_;
        for (uint256 i = 0; i < 4; i++) {
            actors.push(makeAddr(string.concat("actor", vm.toString(i))));
        }
        actors.push(address(new RejectingReceiver())); // makes payments go to `withdrawable` (C-07)
    }

    function actorCount() external view returns (uint256) {
        return actors.length;
    }

    function _actor(uint256 seed) internal view returns (address) {
        return actors[seed % actors.length];
    }

    function _bit(ProofPayEscrow.Status s) internal pure returns (uint256) {
        return 1 << uint8(s);
    }

    /// Returns the first task, from a random start, whose status is in `statusMask`. Returns id 0 IF none.
    function _find(uint256 seed, uint256 statusMask) internal view returns (uint256 id, ProofPayEscrow.Task memory t) {
        uint256 n = escrow.taskCount();
        for (uint256 i = 0; i < n; i++) {
            id = ((seed % n + i) % n) + 1;
            t = escrow.getTask(id);
            if (statusMask & _bit(t.status) != 0) return (id, t);
        }
        return (0, t);
    }

    function createTask(uint256 actorSeed, uint256 amount, uint256 delay) external {
        if (escrow.taskCount() >= MAX_TASKS) return;
        address poster = _actor(actorSeed);
        amount = _bound(amount, escrow.MIN_AMOUNT(), 1 ether);
        delay = _bound(delay, 60, 3 hours);
        vm.deal(poster, poster.balance + amount); // funds the poster, not the escrow
        vm.prank(poster);
        escrow.createTask{ value: amount }(
            "Task", "Description", keccak256(abi.encode("before", actorSeed)), uint64(block.timestamp + delay)
        );
        calls["createTask"]++;
    }

    function acceptTask(uint256 taskSeed, uint256 actorSeed) external {
        (uint256 id, ProofPayEscrow.Task memory t) = _find(taskSeed, _bit(ProofPayEscrow.Status.Open));
        if (id == 0) return;
        address worker = _actor(actorSeed);
        if (worker == t.poster) worker = _actor(actorSeed % actors.length + 1);
        vm.prank(worker);
        try escrow.acceptTask(id) {
            calls["acceptTask"]++;
        } catch { }
    }

    function submitProof(uint256 taskSeed, uint256 proofSeed) external {
        (uint256 id, ProofPayEscrow.Task memory t) = _find(taskSeed, _bit(ProofPayEscrow.Status.Accepted));
        if (id == 0) return;
        vm.prank(t.worker);
        try escrow.submitProof(id, keccak256(abi.encode("after", id, t.attempts, proofSeed))) {
            calls["submitProof"]++;
        } catch { }
    }

    function recordVerdict(uint256 taskSeed, bool pass, uint256 score) external {
        (uint256 id, ProofPayEscrow.Task memory t) = _find(taskSeed, _bit(ProofPayEscrow.Status.Submitted));
        if (id == 0) return;
        vm.prank(verifier);
        try escrow.recordVerdict(id, t.attempts, t.proofHash, pass, uint8(_bound(score, 0, 100)), "reason") {
            calls[pass ? bytes32("verdictPass") : bytes32("verdictFail")]++;
        } catch { }
    }

    function dispute(uint256 taskSeed) external {
        (uint256 id, ProofPayEscrow.Task memory t) = _find(taskSeed, _bit(ProofPayEscrow.Status.Approved));
        if (id == 0) return;
        vm.prank(t.poster);
        try escrow.dispute(id) {
            calls["dispute"]++;
        } catch { }
    }

    function resolveDispute(uint256 taskSeed, bool payWorker) external {
        (uint256 id, ProofPayEscrow.Task memory t) = _find(taskSeed, _bit(ProofPayEscrow.Status.Disputed));
        if (id == 0) return;
        vm.prank(owner);
        try escrow.resolveDispute(id, payWorker) {
            calls["resolveDispute"]++;
        } catch { }
    }

    function expireDispute(uint256 taskSeed, uint256 callerSeed) external {
        (uint256 id, ProofPayEscrow.Task memory t) = _find(taskSeed, _bit(ProofPayEscrow.Status.Disputed));
        if (id == 0) return;
        vm.prank(_actor(callerSeed));
        try escrow.expireDispute(id) {
            calls["expireDispute"]++;
        } catch { }
    }

    function release(uint256 taskSeed, uint256 callerSeed) external {
        (uint256 id, ProofPayEscrow.Task memory t) = _find(taskSeed, _bit(ProofPayEscrow.Status.Approved));
        if (id == 0) return;
        vm.prank(_actor(callerSeed));
        try escrow.release(id) {
            calls["release"]++;
        } catch { }
    }

    function refund(uint256 taskSeed, uint256 chance) external {
        uint256 mask = _bit(ProofPayEscrow.Status.Accepted) | _bit(ProofPayEscrow.Status.Submitted);
        // Open refunds always work, so allow them only in 1 of 4 calls; otherwise few tasks get accepted.
        if (chance % 4 == 0) mask |= _bit(ProofPayEscrow.Status.Open);
        (uint256 id, ProofPayEscrow.Task memory t) = _find(taskSeed, mask);
        if (id == 0) return;
        vm.prank(t.poster);
        try escrow.refund(id) {
            calls["refund"]++;
        } catch { }
    }

    function withdraw(uint256 actorSeed) external {
        address who = _actor(actorSeed);
        if (escrow.withdrawable(who) == 0) return;
        vm.prank(who);
        try escrow.withdraw(sink) {
            calls["withdraw"]++;
        } catch { }
    }

    function warp(uint256 seed) external {
        // Mostly short steps (boundaries), sometimes long jumps (deadlines and arbitration timeouts).
        uint256 step = seed % 8 == 0 ? _bound(seed, 1 hours, 2 hours) : _bound(seed, 1, 120);
        vm.warp(block.timestamp + step);
        calls["warp"]++;
    }
}

/// @notice C-T14: the escrow balance always equals the MON it still owes.
contract ProofPayInvariantTest is Test {
    ProofPayEscrow internal escrow;
    EscrowHandler internal handler;

    function setUp() public {
        vm.warp(1_760_000_000);
        address owner = makeAddr("owner");
        address verifier = makeAddr("verifier");
        vm.prank(owner);
        escrow = new ProofPayEscrow(verifier, 60, 300, 3600);
        handler = new EscrowHandler(escrow, owner, verifier);

        bytes4[] memory selectors = new bytes4[](11);
        selectors[0] = EscrowHandler.createTask.selector;
        selectors[1] = EscrowHandler.acceptTask.selector;
        selectors[2] = EscrowHandler.submitProof.selector;
        selectors[3] = EscrowHandler.recordVerdict.selector;
        selectors[4] = EscrowHandler.dispute.selector;
        selectors[5] = EscrowHandler.resolveDispute.selector;
        selectors[6] = EscrowHandler.expireDispute.selector;
        selectors[7] = EscrowHandler.release.selector;
        selectors[8] = EscrowHandler.refund.selector;
        selectors[9] = EscrowHandler.withdraw.selector;
        selectors[10] = EscrowHandler.warp.selector;
        targetSelector(FuzzSelector({ addr: address(handler), selectors: selectors }));
        targetContract(address(handler));
    }

    /// forge-config: default.invariant.runs = 128
    /// forge-config: default.invariant.depth = 150
    /// forge-config: default.invariant.fail-on-revert = true
    /// C-T14: balance == sum(amount of tasks in Open, Accepted, Submitted, Approved, Disputed) + sum(withdrawable).
    function invariant_CT14_balanceEqualsOwed() public view {
        uint256 owed;
        uint256 n = escrow.taskCount();
        for (uint256 id = 1; id <= n; id++) {
            ProofPayEscrow.Task memory t = escrow.getTask(id);
            if (t.status != ProofPayEscrow.Status.Paid && t.status != ProofPayEscrow.Status.Refunded) {
                owed += t.amount;
            }
        }
        // Only handler actors can be posters or workers, so only they can have a withdrawable credit.
        uint256 m = handler.actorCount();
        for (uint256 i = 0; i < m; i++) {
            owed += escrow.withdrawable(handler.actors(i));
        }
        assertEq(address(escrow).balance, owed);
    }

    /// forge-config: default.invariant.runs = 128
    /// forge-config: default.invariant.depth = 150
    /// forge-config: default.invariant.fail-on-revert = true
    /// Supporting checks: amount is never zero and attempts never pass MAX_ATTEMPTS.
    function invariant_CT14_taskFieldsStayInRange() public view {
        uint256 n = escrow.taskCount();
        for (uint256 id = 1; id <= n; id++) {
            ProofPayEscrow.Task memory t = escrow.getTask(id);
            assertGe(t.amount, escrow.MIN_AMOUNT());
            assertLe(t.attempts, escrow.MAX_ATTEMPTS());
            assertLe(t.score, 100);
        }
    }

    /// Shows how many calls of each kind succeeded (run with -vv).
    function afterInvariant() public {
        string[12] memory keys = [
            "createTask",
            "acceptTask",
            "submitProof",
            "verdictPass",
            "verdictFail",
            "dispute",
            "resolveDispute",
            "expireDispute",
            "release",
            "refund",
            "withdraw",
            "warp"
        ];
        for (uint256 i = 0; i < keys.length; i++) {
            emit log_named_uint(keys[i], handler.calls(bytes32(bytes(keys[i]))));
        }
    }
}
