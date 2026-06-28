import unittest

from val_agent.models import RuleResult
from val_agent.tools.routing import decide_route


class RoutingTests(unittest.TestCase):
    def test_route_stp_when_no_failures(self):
        self.assertEqual(decide_route([RuleResult("A", "Agent", "PASS", "ERROR")]), "STRAIGHT_THROUGH_PROCESSING")

    def test_route_human_for_error_failure(self):
        results = [RuleResult("A", "Agent", "FAIL", "ERROR", error_code="ERR")]
        self.assertEqual(decide_route(results), "HUMAN_IN_THE_LOOP")

    def test_route_critical_for_fatal_failure(self):
        results = [RuleResult("A", "Agent", "FAIL", "FATAL", error_code="ERR")]
        self.assertEqual(decide_route(results), "CRITICAL_REJECTION")


if __name__ == "__main__":
    unittest.main()
