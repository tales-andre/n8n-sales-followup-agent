"""Offline checks of the workflow JSON, seeds and safety rules (no Docker needed).

    python tests/test_workflows.py
"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF_DIR = os.path.join(ROOT, "workflows")


def load(name):
    with open(os.path.join(WF_DIR, name), encoding="utf-8") as f:
        return json.load(f)


ERROR = load("01-error-alert.json")
DAILY = load("02-daily-drafts.json")
QUEUE = load("03-approval-queue.json")
ALL = [ERROR, DAILY, QUEUE]

CUSTOMER_SENDERS = {"Send via SMTP (demo: Mailpit)", "Send via Outlook (Microsoft Graph)"}


def nodes_by_name(wf):
    return {n["name"]: n for n in wf["nodes"]}


def edges(wf):
    """(source, output_index, target) for every connection."""
    for src, conn in wf["connections"].items():
        for out_idx, targets in enumerate(conn.get("main", [])):
            for t in targets or []:
                yield src, out_idx, t["node"]


def paths_to(wf, target):
    """Every path (as a list of (node, output_index)) from a trigger to `target`."""
    incoming = {}
    for src, idx, dst in edges(wf):
        incoming.setdefault(dst, []).append((src, idx))
    found = []

    def walk(node, suffix, seen):
        preds = incoming.get(node, [])
        if not preds:
            found.append(suffix)
            return
        for src, idx in preds:
            if src not in seen:
                walk(src, [(src, idx)] + suffix, seen | {src})

    walk(target, [], {target})
    return found


class WorkflowStructure(unittest.TestCase):
    def test_ids_and_names_are_unique_and_stable(self):
        ids = [wf["id"] for wf in ALL]
        self.assertEqual(len(set(ids)), 3)
        for wf in ALL:
            names = [n["name"] for n in wf["nodes"]]
            self.assertEqual(len(names), len(set(names)), wf["name"])

    def test_every_connection_points_to_an_existing_node(self):
        for wf in ALL:
            names = set(nodes_by_name(wf))
            for src, _, dst in edges(wf):
                self.assertIn(src, names, wf["name"])
                self.assertIn(dst, names, wf["name"])

    def test_every_non_note_node_is_connected(self):
        for wf in ALL:
            linked = set()
            for src, _, dst in edges(wf):
                linked |= {src, dst}
            for n in wf["nodes"]:
                if n["type"] != "n8n-nodes-base.stickyNote":
                    self.assertIn(n["name"], linked, f'{wf["name"]}: "{n["name"]}" is not connected')

    def test_failures_go_to_the_error_workflow(self):
        for wf in (DAILY, QUEUE):
            self.assertEqual(wf["settings"].get("errorWorkflow"), ERROR["id"], wf["name"])
        types = [n["type"] for n in ERROR["nodes"]]
        self.assertIn("n8n-nodes-base.errorTrigger", types)
        self.assertIn("n8n-nodes-base.emailSend", types)

    def test_credentials_match_the_ones_created_at_startup(self):
        with open(os.path.join(ROOT, "n8n", "render-credentials.js"), encoding="utf-8") as f:
            js = f.read()
        known = set(re.findall(r"id: '([A-Za-z0-9]+)'", js))
        for wf in ALL:
            for n in wf["nodes"]:
                for cred in (n.get("credentials") or {}).values():
                    self.assertIn(cred["id"], known, f'{wf["name"]} / {n["name"]}')


class SafetyRules(unittest.TestCase):
    def test_daily_workflow_never_emails_a_customer(self):
        for n in DAILY["nodes"]:
            if n["type"] in ("n8n-nodes-base.emailSend", "n8n-nodes-base.microsoftOutlook"):
                to = n["parameters"].get("toEmail", "")
                self.assertTrue("managerEmail" in to or "alertEmail" in to or to == "={{ $json.to }}",
                                f'{n["name"]} sends to {to}')
        summary = nodes_by_name(DAILY)["Write the morning summary"]["parameters"]["jsCode"]
        self.assertIn("s.managerEmail", summary)

    def test_customer_email_only_after_an_approved_decision(self):
        """Every path to a node that emails a customer passes Record the decision,
        the 'still waiting' check (true) and 'Approved?' (true) and 'Email address looks valid?' (true)."""
        names = nodes_by_name(QUEUE)
        for sender in CUSTOMER_SENDERS:
            self.assertIn(sender, names)
            paths = paths_to(QUEUE, sender)
            self.assertTrue(paths)
            for p in paths:
                hops = dict(p)
                self.assertIn("Record the decision", hops, p)
                self.assertEqual(hops.get("Still waiting? (not already handled)"), 0, p)
                self.assertEqual(hops.get("Approved?"), 0, p)
                self.assertEqual(hops.get("Email address looks valid?"), 0, p)

    def test_send_failures_are_caught_and_alerted(self):
        names = nodes_by_name(QUEUE)
        for sender in CUSTOMER_SENDERS:
            self.assertEqual(names[sender].get("onError"), "continueErrorOutput", sender)
            err_targets = [dst for src, idx, dst in edges(QUEUE) if src == sender and idx == 1]
            self.assertEqual(err_targets, ["Describe the send failure"])
        chain = [(s, d) for s, _, d in edges(QUEUE)]
        self.assertIn(("Describe the send failure", "Mark as failed"), chain)
        self.assertIn(("Mark as failed", "Email an alert naming the account"), chain)

    def test_ai_failures_are_caught_and_alerted(self):
        names = nodes_by_name(DAILY)
        for node in ("Ask the AI to draft", "Read the AI reply"):
            self.assertEqual(names[node].get("onError"), "continueErrorOutput", node)
        chain = [(s, d) for s, _, d in edges(DAILY)]
        self.assertIn(("Mark draft failed (retried tomorrow)", "Email an alert naming the account"), chain)

    def test_offline_template_is_clearly_labeled(self):
        names = nodes_by_name(DAILY)
        stub = names["Draft email (OFFLINE TEMPLATE, no AI)"]["parameters"]["jsCode"]
        self.assertIn("offline template (no AI)", stub)

    def test_no_secrets_in_workflows_or_repo_config(self):
        pattern = re.compile(r"(sk-[A-Za-z0-9]{16,}|sk-ant-|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY)")
        for fname in os.listdir(WF_DIR):
            with open(os.path.join(WF_DIR, fname), encoding="utf-8") as f:
                self.assertIsNone(pattern.search(f.read()), fname)
        # the API key is only referenced through the environment, never stored
        http = nodes_by_name(DAILY)["Ask the AI to draft"]["parameters"]["jsonHeaders"]
        self.assertIn("$env.LLM_API_KEY", http)
        build = nodes_by_name(DAILY)["Build AI request"]["parameters"]["jsCode"]
        self.assertNotIn("LLM_API_KEY", build)

    def test_no_n8n_attribution_footer_in_customer_emails(self):
        for wf in ALL:
            for n in wf["nodes"]:
                if n["type"] == "n8n-nodes-base.emailSend":
                    self.assertIs(n["parameters"]["options"].get("appendAttribution"), False, n["name"])


class SeedsAndSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "db", "init", "01-schema.sql"), encoding="utf-8") as f:
            cls.schema = f.read()
        with open(os.path.join(ROOT, "db", "init", "02-seed.sql"), encoding="utf-8") as f:
            cls.seed = f.read()

    def test_idempotency_guard_exists(self):
        self.assertIn("UNIQUE (account_id, cycle_key)", self.schema)
        self.assertIn("ON CONFLICT ON CONSTRAINT followups_account_id_cycle_key_key", self.schema)

    def test_decision_requires_pending_status_and_token(self):
        body = self.schema.split("FUNCTION decide_followup", 1)[1].split("$fn$;", 1)[0]
        self.assertIn("approval_token::text = p_token", body)
        self.assertIn("status = 'pending_approval'", body)
        self.assertIn("FOR UPDATE", body)

    def test_seed_uses_reserved_example_domains_only(self):
        emails = re.findall(r"'([^' ]*@[^' ]*)'", self.seed)
        self.assertTrue(emails)
        for e in emails:
            self.assertTrue(e.endswith(".example"), e)

    def test_seed_says_it_is_fictional(self):
        self.assertIn("FICTIONAL", self.seed)

    def test_every_workflow_sql_function_exists_in_schema(self):
        for wf in ALL:
            for n in wf["nodes"]:
                if n["type"] == "n8n-nodes-base.postgres":
                    q = n["parameters"]["query"]
                    for fn in re.findall(r"FROM\s+([a-z_]+)\s*[\(;]", q):
                        self.assertRegex(self.schema, rf"(FUNCTION|VIEW) {fn}\b", f'{n["name"]}: {fn}')


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
