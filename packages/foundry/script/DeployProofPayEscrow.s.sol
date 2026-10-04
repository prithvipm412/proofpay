// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "./DeployHelpers.s.sol";
import { ProofPayEscrow } from "../contracts/ProofPayEscrow.sol";

/**
 * @notice Deploy script for ProofPayEscrow (section 9.7).
 * @dev Reads the deploy settings from the shell environment (section 8). No address or key is in this file.
 *      VERIFIER_ADDRESS, DISPUTE_WINDOW, REVIEW_GRACE, ARBITRATION_TIMEOUT
 * Example (Monad testnet, keystore "deployer"):
 *      yarn deploy --network monad_testnet --keystore deployer
 */
contract DeployProofPayEscrow is ScaffoldETHDeploy {
    error UnsupportedChain(uint256 chainId);

    function run() external ScaffoldEthDeployerRunner {
        // Section 4: public deployments only on Monad testnet (10143); local anvil (31337) for tests.
        if (block.chainid != 10143 && block.chainid != 31337) revert UnsupportedChain(block.chainid);

        address verifier = vm.envAddress("VERIFIER_ADDRESS");
        uint64 disputeWindow = uint64(vm.envUint("DISPUTE_WINDOW"));
        uint64 reviewGrace = uint64(vm.envUint("REVIEW_GRACE"));
        uint64 arbitrationTimeout = uint64(vm.envUint("ARBITRATION_TIMEOUT"));

        ProofPayEscrow escrow = new ProofPayEscrow(verifier, disputeWindow, reviewGrace, arbitrationTimeout);
        console.log("ProofPayEscrow deployed at", address(escrow));
        console.log("owner (arbiter)", escrow.owner());
        console.log("verifier", escrow.verifier());
    }
}
