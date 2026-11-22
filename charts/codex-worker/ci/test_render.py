import json
from pathlib import Path
import subprocess
import sys
import unittest

import yaml


CHART = Path(__file__).resolve().parents[1]
FIXTURE = CHART / "ci/test-values.yaml"
ROOT = CHART.parents[1]
MONITORING = ("--set", "monitoring.enabled=true,monitoring.rules.enabled=true",
              "--set", "monitoring.rules.runbookUrl=https://docs.example.invalid/worker/")
EXTERNAL = ("--set", "secrets.type=externalSecret,secrets.workerTokenSecret=,secrets.authStoreTokenSecret=,secrets.gatewayKeySecret=",
            "--set", "secrets.externalSecret.storeName=tenant-store",
            "--set", "secrets.externalSecret.items.workerToken.key=Request bearer",
            "--set", "secrets.externalSecret.items.authStoreToken.key=Auth store",
            "--set", "secrets.externalSecret.items.gatewayKey.key=Gateway key")


def render(*args: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "worker", str(CHART), "--namespace", "test",
         "-f", str(FIXTURE), *args], capture_output=True, text=True, check=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def by_kind(docs: list[dict], kind: str) -> dict:
    return next(doc for doc in docs if doc["kind"] == kind)


class RenderTests(unittest.TestCase):
    def test_single_worker_is_restricted_and_has_no_api_token(self) -> None:
        docs = render()
        deployment = by_kind(docs, "Deployment")
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertEqual(deployment["spec"]["replicas"], 1)
        self.assertEqual(deployment["spec"]["strategy"], {"type": "Recreate"})
        self.assertEqual([c["name"] for c in pod["containers"]], ["worker", "codex"])
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertFalse(by_kind(docs, "ServiceAccount")["automountServiceAccountToken"])
        self.assertEqual(pod["serviceAccountName"], "worker")
        self.assertEqual(pod["terminationGracePeriodSeconds"], 3600)
        self.assertEqual(pod["securityContext"], {
            "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"},
            "fsGroupChangePolicy": "OnRootMismatch",
        })
        self.assertEqual(container["securityContext"], {
            "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        })
        self.assertNotIn("runAsUser", json.dumps(pod))
        self.assertNotIn('"fsGroup"', json.dumps(pod))
        self.assertNotIn("hostPath", json.dumps(pod))
        self.assertNotIn("hostNetwork", pod)
        self.assertNotIn("command", container)
        self.assertNotIn("args", container)
        self.assertEqual(container["readinessProbe"]["tcpSocket"], {"port": "https"})
        self.assertNotIn("livenessProbe", container)
        self.assertEqual(container["resources"], {
            "requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"memory": "2Gi"},
        })

    def test_private_state_uses_retained_pvc_parent(self) -> None:
        docs = render()
        claim = by_kind(docs, "PersistentVolumeClaim")
        self.assertEqual(claim["spec"], {
            "accessModes": ["ReadWriteOncePod"], "storageClassName": "test-block",
            "volumeMode": "Filesystem", "resources": {"requests": {"storage": "1Gi"}},
        })
        annotations = claim["metadata"]["annotations"]
        self.assertEqual(annotations["helm.sh/resource-policy"], "keep")
        self.assertEqual(set(annotations["argocd.argoproj.io/sync-options"].split(",")),
                         {"Prune=false", "Delete=false"})
        pod = by_kind(docs, "Deployment")["spec"]["template"]["spec"]
        mounts = pod["containers"][0]["volumeMounts"]
        self.assertEqual([(m["mountPath"], m.get("subPath")) for m in mounts if not m.get("readOnly")],
                         [("/tmp", None), ("/var/lib/codex/state", "state"),
                          ("/var/lib/codex/exchange", "exchange")])
        self.assertEqual(next(v for v in pod["volumes"] if v["name"] == "state"),
                         {"name": "state", "persistentVolumeClaim": {"claimName": "worker-state"}})
        init = pod["initContainers"][0]
        self.assertEqual(init["command"][-1], "umask 077 && mkdir -p /volume/state /volume/exchange")
        self.assertEqual(init["volumeMounts"], [{"name": "state", "mountPath": "/volume"}])
        docs = render("--set", "storage.existingClaim=restored,storage.storageClass=,storage.accessMode=ReadWriteOnce")
        self.assertNotIn("PersistentVolumeClaim", [d["kind"] for d in docs])
        volumes = by_kind(docs, "Deployment")["spec"]["template"]["spec"]["volumes"]
        self.assertEqual(next(v for v in volumes if v["name"] == "state")["persistentVolumeClaim"]["claimName"], "restored")

    def test_cli_container_shares_only_the_exchange_with_the_worker(self) -> None:
        pod = by_kind(render(), "Deployment")["spec"]["template"]["spec"]
        worker, codex = pod["containers"]
        self.assertEqual(codex["command"], ["node", "scripts/codex-exec-agent.mjs"])
        self.assertEqual(codex["image"], worker["image"])
        self.assertEqual({e["name"]: e.get("value") for e in codex["env"]}, {
            "HOME": "/tmp", "TMPDIR": "/tmp",
            "CODEX_EXCHANGE_DIR": "/var/lib/codex/exchange", "CODEX_AGENT_PORT": "3444",
        })
        self.assertNotIn("envFrom", codex)
        self.assertNotIn("ports", codex)
        self.assertEqual(codex["volumeMounts"], [
            {"name": "codex-tmp", "mountPath": "/tmp"},
            {"name": "state", "mountPath": "/var/lib/codex/exchange", "subPath": "exchange"},
        ])
        self.assertEqual(codex["securityContext"], worker["securityContext"])
        self.assertFalse(pod["enableServiceLinks"])
        self.assertNotIn("shareProcessNamespace", pod)
        self.assertNotIn("hostPID", pod)

    def test_runtime_environment_and_separate_secret_wiring(self) -> None:
        pod = by_kind(render("--set", "image.pullSecrets[0].name=registry-access"), "Deployment")["spec"]["template"]["spec"]
        container = pod["containers"][0]
        env = {entry["name"]: entry for entry in container["env"]}
        self.assertEqual(len(env), len(container["env"]))
        expected = {
            "CODEX_WORKER_HOST": "0.0.0.0", "CODEX_WORKER_PORT": "3443",
            "CODEX_AUTH_STATE_DIR": "/var/lib/codex/state",
            "CODEX_EXCHANGE_DIR": "/var/lib/codex/exchange",
            "CODEX_AGENT_URL": "http://127.0.0.1:3444",
            "CODEX_WORKER_TLS_CERT_FILE": "/etc/codex-worker/tls/tls.crt",
            "CODEX_WORKER_TLS_KEY_FILE": "/etc/codex-worker/tls/tls.key",
            "CI_API_V4_URL": "https://gitlab.example.invalid/api/v4",
            "CI_PROJECT_ID": "101", "CODEX_AUTH_STORE_PROJECT_ID": "102",
            "CODEX_GATEWAY_URL": "https://gateway.example.invalid/chatgpt_passthrough/backend-api/codex",
            "CODEX_MODEL": "test-model-1",
            "HOME": "/tmp", "TMPDIR": "/tmp",
        }
        self.assertEqual({name: entry["value"] for name, entry in env.items() if "value" in entry}, expected)
        for name, secret in (("CODEX_WORKER_TOKEN", "request-token"), ("CODEX_AUTH_STORE_TOKEN", "auth-store-token"),
                             ("CODEX_GATEWAY_KEY", "gateway-key")):
            self.assertEqual(env[name]["valueFrom"]["secretKeyRef"], {"name": secret, "key": "token"})
        self.assertEqual(pod["imagePullSecrets"], [{"name": "registry-access"}])
        self.assertEqual(container["image"], "registry.example.invalid/review/worker:0.0.0-test")

    def test_https_service_and_monitoring_survive_ca_data_replacement(self) -> None:
        docs = render(*MONITORING)
        service = by_kind(docs, "Service")
        self.assertEqual(service["metadata"]["name"], "worker")
        self.assertEqual(service["spec"]["type"], "ClusterIP")
        self.assertEqual(service["spec"]["ports"], [
            {"name": "https", "port": 3443, "targetPort": "https", "protocol": "TCP"},
        ])
        self.assertEqual(service["metadata"]["annotations"],
                         {"service.beta.openshift.io/serving-cert-secret-name": "worker-tls"})
        ca = by_kind(docs, "ConfigMap")
        self.assertEqual(ca["data"], {})
        self.assertEqual(ca["metadata"]["annotations"], {"service.beta.openshift.io/inject-cabundle": "true"})
        endpoint = by_kind(docs, "ServiceMonitor")["spec"]["endpoints"][0]
        self.assertEqual(endpoint, {
            "port": "https", "path": "/metrics", "scheme": "https", "interval": "30s",
            "tlsConfig": {"serverName": "worker.test.svc",
                          "ca": {"configMap": {"name": "worker-service-ca", "key": "service-ca.crt"}}},
        })
        pod = by_kind(docs, "Deployment")["spec"]["template"]["spec"]
        self.assertFalse(any("configMap" in v for v in pod["volumes"]))
        self.assertNotIn("envFrom", pod["containers"][0])
        self.assertEqual(pod["volumes"][3]["secret"]["secretName"], "worker-tls")

    def test_portable_tls_uses_existing_secret_and_release_local_ca(self) -> None:
        docs = render(*MONITORING, "--set", "tls.openshift.enabled=false,tls.openshift.injectServiceCa=false",
                      "--set", "tls.existingSecret=custom-tls,tls.caConfigMap.name=custom-ca,tls.caConfigMap.key=bundle.pem")
        self.assertNotIn("ConfigMap", [d["kind"] for d in docs])
        self.assertNotIn("annotations", by_kind(docs, "Service")["metadata"])
        endpoint = by_kind(docs, "ServiceMonitor")["spec"]["endpoints"][0]
        self.assertEqual(endpoint["tlsConfig"]["ca"]["configMap"], {"name": "custom-ca", "key": "bundle.pem"})
        pod = by_kind(docs, "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(pod["volumes"][3]["secret"]["secretName"], "custom-tls")
        self.assertTrue(pod["containers"][0]["volumeMounts"][3]["readOnly"])

    def test_network_policy_selects_callers_and_dns_protocols_per_port(self) -> None:
        docs = render(*MONITORING, "--set", "networkPolicy.egress.dnsPorts={53,5353}")
        policy = by_kind(docs, "NetworkPolicy")["spec"]
        self.assertEqual(policy["policyTypes"], ["Ingress", "Egress"])
        self.assertEqual([r["ports"] for r in policy["ingress"]],
                         [[{"protocol": "TCP", "port": 3443}]] * 2)
        self.assertEqual([r["from"][0]["namespaceSelector"] for r in policy["ingress"]], [
            {"matchLabels": {"kubernetes.io/metadata.name": "test-runners"}},
            {"matchLabels": {"kubernetes.io/metadata.name": "test-monitoring"}},
        ])
        self.assertEqual(policy["egress"][0]["ports"], [
            {"protocol": protocol, "port": port} for port in (53, 5353) for protocol in ("UDP", "TCP")
        ])
        self.assertEqual(policy["egress"][0]["to"], [
            {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "test-dns"}}},
        ])
        self.assertEqual(policy["egress"][1], {
            "to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}, {"ipBlock": {"cidr": "::/0"}}],
            "ports": [{"protocol": "TCP", "port": 443}],
        })
        self.assertNotIn("NetworkPolicy", [d["kind"] for d in render("--set", "networkPolicy.enabled=false")])

    def test_external_credentials_have_independent_names_and_precedence(self) -> None:
        docs = render(*EXTERNAL)
        external = [d for d in docs if d["kind"] == "ExternalSecret"]
        self.assertEqual(len(external), 3)
        self.assertNotIn("Secret", [d["kind"] for d in docs])
        for resource, suffix, remote in zip(external, ("worker-token", "auth-store-token", "gateway-key"),
                                            ("Request bearer", "Auth store", "Gateway key"), strict=True):
            spec = resource["spec"]
            self.assertEqual(resource["metadata"]["name"], "worker-" + suffix)
            self.assertEqual(spec["target"]["name"], "worker-" + suffix)
            self.assertEqual(spec["target"]["template"]["data"], {"token": "{{ .value }}"})
            self.assertEqual(spec["secretStoreRef"], {"name": "tenant-store", "kind": "SecretStore"})
            self.assertEqual(spec["data"][0]["remoteRef"]["key"], remote)
            self.assertEqual(spec["data"][0]["remoteRef"]["property"], "login.password")
        overridden = render(*EXTERNAL, "--set", "secrets.workerTokenSecret=caller-token",
                            "--set", "secrets.externalSecret.storeKind=ClusterSecretStore")
        remaining = [d for d in overridden if d["kind"] == "ExternalSecret"]
        self.assertEqual([d["metadata"]["name"] for d in remaining], ["worker-auth-store-token", "worker-gateway-key"])
        self.assertEqual(remaining[0]["spec"]["secretStoreRef"]["kind"], "ClusterSecretStore")
        original = by_kind(docs, "Deployment")["spec"]["template"]["metadata"]["annotations"]
        changed = by_kind(render(*EXTERNAL, "--set", "secrets.externalSecret.items.workerToken.key=Rotated"), "Deployment")
        self.assertNotEqual(original, changed["spec"]["template"]["metadata"]["annotations"])
        env = by_kind(overridden, "Deployment")["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertEqual(next(e for e in env if e["name"] == "CODEX_WORKER_TOKEN")["valueFrom"]["secretKeyRef"],
                         {"name": "caller-token", "key": "token"})

    def test_namespaced_objects_pass_real_label_checker(self) -> None:
        for args in ((), EXTERNAL + MONITORING):
            with self.subTest(args=args):
                docs = render(*args)
                self.assertTrue(all(d["metadata"]["namespace"] == "test" for d in docs))
                self.assertLessEqual({d["kind"] for d in docs}, {
                    "Deployment", "Service", "ServiceAccount", "PersistentVolumeClaim",
                    "ConfigMap", "NetworkPolicy", "ExternalSecret", "ServiceMonitor", "PrometheusRule",
                })
                result = subprocess.run([sys.executable, str(ROOT / "ci/check_labels.py"), "codex-worker"],
                                        input="".join("---\n# Source: codex-worker/templates/test.yaml\n" + yaml.safe_dump(d) for d in docs),
                                        text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual({d["kind"] for d in render()}, {
            "Deployment", "Service", "ServiceAccount", "PersistentVolumeClaim", "ConfigMap", "NetworkPolicy",
        })

    def test_render_fails_closed_on_missing_and_invalid_configuration(self) -> None:
        invalid = (
            "image.repository=", "image.tag=", "image.tag=latest", "image.tag=bad:tag",
            "image.digest=sha256:" + "a" * 64,
            "gitlab.apiV4Url=http://gitlab.example.invalid/api/v4", "gitlab.projectId=", "authStore.projectId=",
            "gitlab.apiV4Url=https://gitlab.example.invalid/api/v4/",
            "authStore.endpoint=https://store.example.invalid", "secrets.workerTokenSecret=", "secrets.authStoreTokenSecret=",
            "secrets.gatewayKeySecret=", "gateway.url=", "gateway.model=", "gateway.model=bad model", "gateway.url=http://gateway.example.invalid/codex",
            "gateway.url=https://gateway.example.invalid/codex/", "gateway.url=https://user@gateway.example.invalid/codex",
            "storage.storageClass=", "storage.accessMode=ReadWriteMany", "tls.existingSecret=conflict",
            "tls.openshift.enabled=false", "monitoring.rules.enabled=true",
            "networkPolicy.runnerNamespaceSelector=null", "networkPolicy.egress.dnsNamespaceSelector=null",
            "networkPolicy.egress.dnsPorts={0}", "networkPolicy.egress.dnsPorts={65536}",
            "replicas=2", "resources.requests.cpu=", "resources.limits.memory=",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(subprocess.CalledProcessError):
                    render("--set", value)
        for args in (
            ("--set-string", "authStore.projectId=101"),
            ("--set-json", 'image.pullSecrets=[{"name":""}]'),
            (*EXTERNAL, "--set", "secrets.externalSecret.storeName="),
            (*EXTERNAL, "--set", "secrets.externalSecret.items.authStoreToken.key="),
            (*EXTERNAL, "--set", "secrets.externalSecret.items.gatewayKey.key="),
            (*MONITORING, "--set", "monitoring.rules.runbookUrl="),
            (*MONITORING, "--set", "networkPolicy.monitoringNamespaceSelector=null"),
            (*MONITORING, "--set", "tls.openshift.enabled=false,tls.openshift.injectServiceCa=false,tls.existingSecret=custom"),
        ):
            with self.subTest(args=args), self.assertRaises(subprocess.CalledProcessError):
                render(*args)
        result = subprocess.run(["helm", "template", "worker", str(CHART)], capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("image", result.stderr)


if __name__ == "__main__":
    unittest.main()
