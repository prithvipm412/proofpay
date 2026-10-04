// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice C-T9: an account that calls back into the escrow when it receives MON.
/// Tests act as it with `vm.prank` and set the call to repeat with `setReentry`.
/// It tries the call one time only. It records the result in an event, not in storage,
/// so that the attempt fits inside the 50_000 gas of `_pay` (C-07).
contract ReentrantReceiver {
    event ReentryResult(bool ok);

    // target and rejectPayments share one storage slot (fewer cold reads inside 50_000 gas).
    address public target;
    bool public rejectPayments;
    uint256 public reentryAttempts;
    bytes public reentryData;

    function setReentry(address target_, bytes calldata data) external {
        target = target_;
        reentryData = data;
    }

    function setRejectPayments(bool reject) external {
        rejectPayments = reject;
    }

    receive() external payable {
        if (rejectPayments) revert("ReentrantReceiver: rejecting");
        if (reentryAttempts == 0 && target != address(0)) {
            reentryAttempts = 1;
            (bool ok,) = target.call(reentryData);
            emit ReentryResult(ok);
        }
    }
}
