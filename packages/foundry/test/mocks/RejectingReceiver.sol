// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice C-T8: an account that rejects every MON payment. Tests act as it with `vm.prank`.
contract RejectingReceiver {
    receive() external payable {
        revert("RejectingReceiver");
    }
}
