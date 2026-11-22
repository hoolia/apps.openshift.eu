from copy import deepcopy
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

import yaml


CHART = Path(__file__).resolve().parents[1]
RUNBOOK = "https://docs.example.invalid/worker/"
ANNOTATIONS = {
    "CodexWorkerReviewFailed": {
        "summary": "Codex worker reviews are failing",
        "description": "At least one review failed during the configured failure window.",
        "runbook_url": RUNBOOK + "#codexworkerreviewfailed",
    },
    "CodexWorkerReviewStalled": {
        "summary": "Codex worker has a stalled review",
        "description": "A pending review exceeds the configured oldest-review age.",
        "runbook_url": RUNBOOK + "#codexworkerreviewstalled",
    },
    "CodexWorkerUnavailable": {
        "summary": "Codex worker metrics are unavailable",
        "description": "HTTPS metrics scrapes are failing or the scrape target is absent.",
        "runbook_url": RUNBOOK + "#codexworkerunavailable",
    },
    "CodexWorkerSessionNotMirrored": {
        "summary": "Codex worker session is not mirrored",
        "description": "A review finished but its refreshed session was not written to the auth store.",
        "runbook_url": RUNBOOK + "#codexworkersessionnotmirrored",
    },
}


def rule_tests(namespace: str, service: str) -> list[dict]:
    labels = f'namespace="{namespace}",service="{service}"'

    def series(metric: str, values: str, extra: str = "") -> dict:
        return {"series": f"{metric}{{{labels}{extra}}}", "values": values}

    def expected(name: str) -> list[dict]:
        return [{"exp_labels": {"namespace": namespace, "service": service, "severity": "warning"},
                 "exp_annotations": ANNOTATIONS[name]}]

    def checks(at: str, firing: str = "") -> list[dict]:
        return [{"eval_time": at, "alertname": name,
                 "exp_alerts": expected(name) if name == firing else []} for name in ANNOTATIONS]

    healthy = series("up", "1+0x40")
    unrelated = [
        {"series": f'codex_worker_reviews_total{{namespace="unrelated",service="{service}",result="failure"}}', "values": "0+1x40"},
        {"series": f'codex_worker_pending_reviews{{namespace="{namespace}",service="unrelated"}}', "values": "1+0x40"},
        {"series": f'codex_worker_oldest_review_age_seconds{{namespace="{namespace}",service="unrelated"}}', "values": "9999+0x40"},
        {"series": f'up{{namespace="unrelated",service="{service}"}}', "values": "0+0x40"},
        {"series": f'codex_worker_session_mirror_failures_total{{namespace="unrelated",service="{service}"}}', "values": "0+1x40"},
    ]
    return [
        {
            "name": "healthy successes and unrelated failures never alert",
            "interval": "1m",
            "input_series": [healthy, *unrelated,
                             series("codex_worker_reviews_total", "0+1x40", ',result="success"'),
                             series("codex_worker_reviews_total", "0+0x40", ',result="failure"'),
                             series("codex_worker_pending_reviews", "0+0x40"),
                             series("codex_worker_oldest_review_age_seconds", "0+0x40")],
            "alert_rule_test": checks("6m") + checks("30m"),
        },
        {
            "name": "failures alert after five minutes and recover outside the window",
            "interval": "1m",
            "input_series": [healthy, series("codex_worker_reviews_total", "0+1x10 10+0x30", ',result="failure"')],
            "alert_rule_test": checks("4m") + checks("6m", "CodexWorkerReviewFailed") + checks("30m"),
        },
        {
            "name": "an unmirrored session alerts after five minutes and recovers outside the window",
            "interval": "1m",
            "input_series": [healthy, series("codex_worker_session_mirror_failures_total", "0+1x10 10+0x30")],
            "alert_rule_test": checks("4m") + checks("6m", "CodexWorkerSessionNotMirrored") + checks("30m"),
        },
        {
            "name": "pending old reviews stall and recover",
            "interval": "1m",
            "input_series": [healthy, series("codex_worker_pending_reviews", "1+0x10 0+0x30"),
                             series("codex_worker_oldest_review_age_seconds", "1900+0x10 0+0x30")],
            "alert_rule_test": checks("4m") + checks("6m", "CodexWorkerReviewStalled") + checks("20m"),
        },
        {
            "name": "old age without pending reviews never stalls",
            "interval": "1m",
            "input_series": [healthy, series("codex_worker_pending_reviews", "0+0x40"),
                             series("codex_worker_oldest_review_age_seconds", "1900+0x40")],
            "alert_rule_test": checks("6m"),
        },
        {
            "name": "failed scrapes alert after five minutes and recover",
            "interval": "1m",
            "input_series": [series("up", "0+0x10 1+0x30")],
            "alert_rule_test": checks("4m") + checks("6m", "CodexWorkerUnavailable") + checks("20m"),
        },
        {
            "name": "absent target alerts despite unrelated targets",
            "interval": "1m",
            "input_series": unrelated,
            "alert_rule_test": checks("4m") + checks("6m", "CodexWorkerUnavailable"),
        },
    ]


class RuleTests(unittest.TestCase):
    def test_rendered_rules_and_mutation_regressions(self) -> None:
        for namespace, service in (("test", "worker"), ("tenant-b", "second-worker")):
            with self.subTest(namespace=namespace, service=service):
                rendered = subprocess.run([
                    "helm", "template", service, str(CHART), "--namespace", namespace,
                    "-f", str(CHART / "ci/test-values.yaml"),
                    "--set", "monitoring.enabled=true,monitoring.rules.enabled=true",
                    "--set", "monitoring.rules.runbookUrl=" + RUNBOOK,
                ], capture_output=True, text=True, check=True)
                rule = next(d for d in yaml.safe_load_all(rendered.stdout) if d and d["kind"] == "PrometheusRule")
                with TemporaryDirectory(prefix="codex-worker-rules-") as directory:
                    path = Path(directory)
                    rules_file = path / "rules.yaml"
                    tests_file = path / "tests.yaml"
                    rules_file.write_text(yaml.safe_dump(rule["spec"]))
                    tests_file.write_text(yaml.safe_dump({
                        "rule_files": [str(rules_file)], "evaluation_interval": "1m",
                        "tests": rule_tests(namespace, service),
                    }))
                    checked = subprocess.run(["promtool", "check", "rules", str(rules_file)],
                                             capture_output=True, text=True, check=False)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    passed = subprocess.run(["promtool", "test", "rules", str(tests_file)],
                                            capture_output=True, text=True, check=False)
                    self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
                    print(f"GREEN {namespace}/{service}: seven rule scenarios", flush=True)
                    mutations = (
                        (0, 'result="failure"', 'result="success"', "failure result selector"),
                        (1, "> 1800", "> 99999", "stall threshold"),
                        (1, " > 0", " >= 0", "pending reviews guard"),
                        (2, "== 0", "== 2", "failed scrape detection"),
                        (2, "\nor\n", "\nand\n", "absent target branch"),
                        (0, f'namespace="{namespace}"', 'namespace="unrelated"', "tenant namespace scope"),
                        (0, f'service="{service}"', 'service="unrelated"', "release service scope"),
                        (3, "> 0", "> 99", "mirror failure threshold"),
                        (3, f'namespace="{namespace}"', 'namespace="unrelated"', "mirror failure namespace scope"),
                    )
                    for index, old, new, name in mutations:
                        mutated = deepcopy(rule["spec"])
                        original = mutated["groups"][0]["rules"][index]["expr"]
                        replacement = original.replace(old, new)
                        self.assertNotEqual(original, replacement, name)
                        mutated["groups"][0]["rules"][index]["expr"] = replacement
                        rules_file.write_text(yaml.safe_dump(mutated))
                        checked = subprocess.run(["promtool", "check", "rules", str(rules_file)],
                                                 capture_output=True, text=True, check=False)
                        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                        failed = subprocess.run(["promtool", "test", "rules", str(tests_file)],
                                                capture_output=True, text=True, check=False)
                        self.assertNotEqual(failed.returncode, 0, f"Regression was not detected: {name}")
                        print(f"RED confirmed {namespace}/{service}: {name}", flush=True)


if __name__ == "__main__":
    unittest.main()
