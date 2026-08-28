import hashlib

import pytest

from actl.domain.audit.merkle import leaf_hash, merkle_root


def _h(label: str) -> bytes:
    return hashlib.sha256(label.encode()).digest()


def test_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="zero entries"):
        merkle_root([])


def test_single_leaf_root_is_its_own_leaf_hash() -> None:
    e = _h("e1")
    assert merkle_root([e]) == leaf_hash(e)


def test_two_leaves_root_is_node_hash_of_both_leaf_hashes() -> None:
    a, b = _h("a"), _h("b")
    expected = hashlib.sha256(b"\x01" + leaf_hash(a) + leaf_hash(b)).digest()
    assert merkle_root([a, b]) == expected


def test_odd_count_promotes_lone_node_unchanged() -> None:
    """RFC 6962: a lone trailing node is promoted, not duplicated."""
    a, b, c = _h("a"), _h("b"), _h("c")
    la, lb, lc = leaf_hash(a), leaf_hash(b), leaf_hash(c)
    level1_ab = hashlib.sha256(b"\x01" + la + lb).digest()
    # lc is promoted unchanged to pair with level1_ab at the next level
    expected = hashlib.sha256(b"\x01" + level1_ab + lc).digest()
    assert merkle_root([a, b, c]) == expected


def test_is_deterministic() -> None:
    entries = [_h(str(i)) for i in range(64)]
    assert merkle_root(entries) == merkle_root(entries)


def test_node_hashing_is_domain_separated_from_plain_concatenation() -> None:
    """The 0x01 node prefix must actually change the output — otherwise an
    attacker could relabel an internal node as a leaf (the classic
    unprefixed-Merkle-tree ambiguity this construction exists to prevent)."""
    a, b = _h("a"), _h("b")
    root = merkle_root([a, b])
    unprefixed = hashlib.sha256(leaf_hash(a) + leaf_hash(b)).digest()
    assert root != unprefixed


def test_sensitive_to_order() -> None:
    a, b, c = _h("a"), _h("b"), _h("c")
    assert merkle_root([a, b, c]) != merkle_root([c, b, a])


def test_sensitive_to_membership() -> None:
    a, b, c = _h("a"), _h("b"), _h("c")
    assert merkle_root([a, b]) != merkle_root([a, b, c])
