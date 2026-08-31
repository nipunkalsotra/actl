// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {AuditCheckpointAnchor} from "../src/AuditCheckpointAnchor.sol";

contract AuditCheckpointAnchorTest is Test {
    AuditCheckpointAnchor internal anchorContract;
    address internal owner = address(this);
    address internal stranger = address(0xBEEF);

    bytes32 internal chainId = keccak256("actl.audit_log");
    bytes32 internal root1 = keccak256("root-1");
    bytes32 internal root2 = keccak256("root-2");

    function setUp() public {
        anchorContract = new AuditCheckpointAnchor();
    }

    function test_AnchorSuccess_StoresRootAndEmitsEvent() public {
        vm.expectEmit(true, true, true, true);
        emit AuditCheckpointAnchor.CheckpointAnchored(chainId, 1, 64, root1, uint64(block.timestamp));

        anchorContract.anchor(chainId, 1, 64, root1);

        (bytes32 storedRoot, uint64 anchoredAt) = anchorContract.getCheckpoint(chainId, 1, 64);
        assertEq(storedRoot, root1);
        assertEq(anchoredAt, uint64(block.timestamp));
    }

    function test_RevertsForUnauthorizedWriter() public {
        vm.prank(stranger);
        vm.expectRevert(AuditCheckpointAnchor.NotOwner.selector);
        anchorContract.anchor(chainId, 1, 64, root1);
    }

    function test_RevertsForInvalidZeroRoot() public {
        vm.expectRevert(AuditCheckpointAnchor.InvalidRoot.selector);
        anchorContract.anchor(chainId, 1, 64, bytes32(0));
    }

    function test_RevertsForInvalidRange() public {
        vm.expectRevert(AuditCheckpointAnchor.InvalidRange.selector);
        anchorContract.anchor(chainId, 64, 1, root1);
    }

    function test_IdempotentDuplicateSubmissionSucceedsSilently() public {
        anchorContract.anchor(chainId, 1, 64, root1);

        // Second identical submission must not revert and must not change
        // stored state or emit a second event (§28 P11 instruction 4:
        // "retried deliveries must not submit duplicate anchors").
        vm.recordLogs();
        anchorContract.anchor(chainId, 1, 64, root1);
        assertEq(vm.getRecordedLogs().length, 0);

        (bytes32 storedRoot, uint64 anchoredAt) = anchorContract.getCheckpoint(chainId, 1, 64);
        assertEq(storedRoot, root1);
        assertEq(anchoredAt, uint64(block.timestamp));
    }

    function test_RevertsForConflictingRootOnSameRange() public {
        anchorContract.anchor(chainId, 1, 64, root1);

        vm.expectRevert(abi.encodeWithSelector(AuditCheckpointAnchor.ConflictingRoot.selector, root1));
        anchorContract.anchor(chainId, 1, 64, root2);

        // The original root must survive the rejected conflicting write.
        (bytes32 storedRoot,) = anchorContract.getCheckpoint(chainId, 1, 64);
        assertEq(storedRoot, root1);
    }

    function test_ReadFunctionIsPubliclyReadableByAnyThirdParty() public {
        anchorContract.anchor(chainId, 1, 64, root1);

        vm.prank(stranger);
        (bytes32 storedRoot, uint64 anchoredAt) = anchorContract.getCheckpoint(chainId, 1, 64);
        assertEq(storedRoot, root1);
        assertGt(anchoredAt, 0);
    }

    function test_UnanchoredRangeReadsAsZero() public view {
        (bytes32 storedRoot, uint64 anchoredAt) = anchorContract.getCheckpoint(chainId, 999, 1063);
        assertEq(storedRoot, bytes32(0));
        assertEq(anchoredAt, 0);
    }

    function test_DifferentRangesOnSameChainIdAreIndependent() public {
        anchorContract.anchor(chainId, 1, 64, root1);
        anchorContract.anchor(chainId, 65, 128, root2);

        (bytes32 root1Stored,) = anchorContract.getCheckpoint(chainId, 1, 64);
        (bytes32 root2Stored,) = anchorContract.getCheckpoint(chainId, 65, 128);
        assertEq(root1Stored, root1);
        assertEq(root2Stored, root2);
    }
}
