// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24; // C-01

import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";
import { Ownable2Step } from "@openzeppelin/contracts/access/Ownable2Step.sol";
import { ReentrancyGuard } from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title ProofPayEscrow
/// @notice Centralized escrow with automated visual checks and duplicate-evidence detection.
/// The verifier key and the arbiter (the contract owner) are trusted parties. This is a hackathon trust assumption.
/// @dev M1 scope: C-01 to C-15 and C-23. Verdicts, disputes, payouts and withdrawals come in M2.
contract ProofPayEscrow is Ownable2Step, ReentrancyGuard {
    // ---------------------------------------------------------------- types (9.2)

    enum Status {
        Open,
        Accepted,
        Submitted,
        Approved,
        Disputed,
        Paid,
        Refunded
    }

    struct Task {
        address poster;
        address worker;
        uint256 amount; // original reward; never set to zero
        bytes32 beforeHash;
        bytes32 proofHash; // proof hash of the latest attempt
        uint64 createdAt;
        uint64 submitBy;
        uint64 reviewBy;
        uint64 disputeUntil;
        uint8 attempts;
        uint8 score;
        Status status;
        string title;
        string description;
    }

    // ---------------------------------------------------------------- constants (C-11)

    uint8 public constant MAX_ATTEMPTS = 3;
    uint256 public constant MIN_AMOUNT = 0.001 ether;
    uint256 public constant MAX_TITLE = 80; // bytes
    uint256 public constant MAX_DESCRIPTION = 500; // bytes
    uint256 public constant MAX_REASON = 120; // bytes
    uint64 public constant MIN_SUBMIT_DELAY = 60 seconds;
    uint64 public constant MAX_SUBMIT_DELAY = 30 days;

    // Allowed ranges for the constructor time values (C-09, section 8).
    uint64 public constant MIN_DISPUTE_WINDOW = 60 seconds;
    uint64 public constant MAX_DISPUTE_WINDOW = 7 days;
    uint64 public constant MIN_REVIEW_GRACE = 300 seconds;
    uint64 public constant MAX_REVIEW_GRACE = 7 days;
    uint64 public constant MIN_ARBITRATION_TIMEOUT = 3600 seconds;
    uint64 public constant MAX_ARBITRATION_TIMEOUT = 30 days;

    // ---------------------------------------------------------------- storage (9.2)

    mapping(uint256 => Task) private tasks;
    mapping(address => uint256) public withdrawable;
    uint256 public taskCount;
    address public verifier;
    uint64 public immutable disputeWindow;
    uint64 public immutable reviewGrace;
    uint64 public immutable arbitrationTimeout;

    // ---------------------------------------------------------------- events (C-24, M1 subset)

    event TaskCreated(
        uint256 indexed id, address indexed poster, uint256 amount, bytes32 beforeHash, uint64 submitBy, uint64 reviewBy
    );
    event TaskAccepted(uint256 indexed id, address indexed worker);
    event ProofSubmitted(uint256 indexed id, uint8 attempt, bytes32 proofHash);
    event PaymentDeferred(address indexed to, uint256 amount);
    event VerifierChanged(address indexed oldVerifier, address indexed newVerifier);

    // ---------------------------------------------------------------- errors (C-25, M1 subset)

    error NotWorker();
    error PosterCannotAccept();
    error WrongStatus(Status expected, Status actual);
    error AmountTooLow();
    error ZeroHash();
    error SameAsBefore();
    error InvalidDeadline();
    error SubmitClosed();
    error TooManyAttempts();
    error TextTooLong();
    error EmptyText();
    error TaskNotFound();
    error ZeroAddress();
    error InvalidConfig();
    error RenounceDisabled();

    // ---------------------------------------------------------------- modifiers

    /// @dev C-12
    modifier taskExists(uint256 id) {
        if (id == 0 || id > taskCount) revert TaskNotFound();
        _;
    }

    // ---------------------------------------------------------------- constructor (C-02, C-09)

    constructor(address verifier_, uint64 disputeWindow_, uint64 reviewGrace_, uint64 arbitrationTimeout_)
        Ownable(msg.sender)
    {
        if (verifier_ == address(0)) revert ZeroAddress();
        if (disputeWindow_ < MIN_DISPUTE_WINDOW || disputeWindow_ > MAX_DISPUTE_WINDOW) revert InvalidConfig();
        if (reviewGrace_ < MIN_REVIEW_GRACE || reviewGrace_ > MAX_REVIEW_GRACE) revert InvalidConfig();
        if (arbitrationTimeout_ < MIN_ARBITRATION_TIMEOUT || arbitrationTimeout_ > MAX_ARBITRATION_TIMEOUT) {
            revert InvalidConfig();
        }
        verifier = verifier_;
        disputeWindow = disputeWindow_;
        reviewGrace = reviewGrace_;
        arbitrationTimeout = arbitrationTimeout_;
    }

    // ---------------------------------------------------------------- admin

    /// @dev C-03
    function renounceOwnership() public pure override {
        revert RenounceDisabled();
    }

    /// @dev C-10
    function setVerifier(address newVerifier) external onlyOwner {
        if (newVerifier == address(0)) revert ZeroAddress();
        address old = verifier;
        verifier = newVerifier;
        emit VerifierChanged(old, newVerifier);
    }

    // ---------------------------------------------------------------- task flow

    /// @dev C-13
    function createTask(string calldata title, string calldata description, bytes32 beforeHash, uint64 submitBy)
        external
        payable
        returns (uint256 id)
    {
        if (msg.value < MIN_AMOUNT) revert AmountTooLow();
        if (beforeHash == bytes32(0)) revert ZeroHash();
        uint256 len = bytes(title).length;
        if (len == 0) revert EmptyText();
        if (len > MAX_TITLE) revert TextTooLong();
        len = bytes(description).length;
        if (len == 0) revert EmptyText();
        if (len > MAX_DESCRIPTION) revert TextTooLong();
        uint64 nowTs = uint64(block.timestamp);
        if (submitBy < nowTs + MIN_SUBMIT_DELAY || submitBy > nowTs + MAX_SUBMIT_DELAY) revert InvalidDeadline();

        id = ++taskCount; // C-08: IDs start at 1
        Task storage t = tasks[id];
        t.poster = msg.sender;
        t.amount = msg.value;
        t.beforeHash = beforeHash;
        t.createdAt = nowTs;
        t.submitBy = submitBy;
        t.reviewBy = submitBy + reviewGrace;
        t.status = Status.Open;
        t.title = title;
        t.description = description;

        emit TaskCreated(id, msg.sender, msg.value, beforeHash, submitBy, t.reviewBy);
    }

    /// @dev C-14
    function acceptTask(uint256 id) external taskExists(id) {
        Task storage t = tasks[id];
        if (msg.sender == t.poster) revert PosterCannotAccept();
        if (t.status != Status.Open) revert WrongStatus(Status.Open, t.status);
        if (block.timestamp >= t.submitBy) revert SubmitClosed();

        t.worker = msg.sender;
        t.status = Status.Accepted;
        emit TaskAccepted(id, msg.sender);
    }

    /// @dev C-15
    function submitProof(uint256 id, bytes32 proofHash) external taskExists(id) {
        Task storage t = tasks[id];
        if (msg.sender != t.worker) revert NotWorker();
        if (t.status != Status.Accepted) revert WrongStatus(Status.Accepted, t.status);
        if (block.timestamp >= t.submitBy) revert SubmitClosed();
        if (t.attempts >= MAX_ATTEMPTS) revert TooManyAttempts();
        if (proofHash == bytes32(0)) revert ZeroHash();
        if (proofHash == t.beforeHash) revert SameAsBefore();

        t.proofHash = proofHash;
        uint8 attempt = ++t.attempts;
        t.status = Status.Submitted;
        emit ProofSubmitted(id, attempt, proofHash);
    }

    // ---------------------------------------------------------------- views

    /// @dev C-23
    function getTask(uint256 id) external view taskExists(id) returns (Task memory) {
        return tasks[id];
    }

    // ---------------------------------------------------------------- payments

    /// @dev C-07. Used by the M2 payout functions. A failed send credits `withdrawable` and never reverts.
    function _pay(address to, uint256 amount) internal {
        (bool ok,) = payable(to).call{ value: amount, gas: 50_000 }("");
        if (!ok) {
            withdrawable[to] += amount;
            emit PaymentDeferred(to, amount);
        }
    }
}
