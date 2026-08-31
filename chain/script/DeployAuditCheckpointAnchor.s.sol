// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {AuditCheckpointAnchor} from "../src/AuditCheckpointAnchor.sol";

/// @notice §28 P11 opt-in deployment. Never run implicitly -- see
/// docs/monad-testnet.md for the keystore-based `forge script` invocation.
/// Reads the signer from `--account <keystore-name>` (Foundry's own
/// keystore flow, `cast wallet import`), never from a plaintext private
/// key or env var.
contract DeployAuditCheckpointAnchor is Script {
    function run() external returns (AuditCheckpointAnchor deployed) {
        vm.startBroadcast();
        deployed = new AuditCheckpointAnchor();
        vm.stopBroadcast();

        console.log("AuditCheckpointAnchor deployed at:", address(deployed));
        console.log("owner:", deployed.owner());
    }
}
