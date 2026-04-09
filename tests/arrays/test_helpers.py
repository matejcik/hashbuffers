"""Unit tests for arrays.py helper functions — limits_to_cumulative, limits_to_individual."""

from hashbuffers.arrays import limits_to_cumulative, limits_to_individual
from hashbuffers.codec import Link


def _link(digest_byte: int, limit: int) -> Link:
    return Link(bytes([digest_byte]) * 32, limit)


class TestLimitsToCumulative:
    def test_single_link(self):
        links = [_link(0, 5)]
        result = limits_to_cumulative(links)
        assert result[0].limit == 5
        assert result[0].digest == links[0].digest

    def test_multiple_links(self):
        links = [_link(0, 3), _link(1, 5), _link(2, 2)]
        result = limits_to_cumulative(links)
        assert [l.limit for l in result] == [3, 8, 10]


class TestLimitsToIndividual:
    def test_single_link(self):
        cumulative = [_link(0, 5)]
        result = limits_to_individual(cumulative)
        assert result[0].limit == 5
        assert result[0].digest == cumulative[0].digest

    def test_multiple_links(self):
        cumulative = [_link(0, 3), _link(1, 8), _link(2, 10)]
        result = limits_to_individual(cumulative)
        assert [l.limit for l in result] == [3, 5, 2]

    def test_roundtrip(self):
        """limits_to_cumulative and limits_to_individual are inverses."""
        individual = [_link(0, 3), _link(1, 5), _link(2, 2)]
        cumulative = limits_to_cumulative(individual)
        back = limits_to_individual(cumulative)
        assert [l.limit for l in back] == [3, 5, 2]
