"""Durable intent before effects; a lost acknowledgement remains unknown."""
import hashlib
import json
from pathlib import Path
import posixpath
from urllib.parse import urlsplit, urlunsplit

from .state import append_event, hash_value, identifier, read_record, text, transaction, write_record


class OperationLedger:
    def __init__(self, store):
        self.store = store

    def root_task_id(self, task_id):
        """Resolve immutable managed-task ancestry without changing state."""
        seen = set()
        while task_id not in seen:
            seen.add(task_id)
            parent = self.store.task(task_id)["contract"].get("parent_task_id")
            if not parent:
                return task_id
            task_id = parent
        raise ValueError("Parent task cycle")

    @staticmethod
    def _effect_identity(intent, *, logical=True):
        action = intent.get("action")
        if not isinstance(action, dict):
            return hash_value(intent)
        # Observer commands and timeout changes do not create a new external
        # effect. A local workspace label cannot renew a managed-root attempt.
        effect = {key: value for key, value in intent.items()
                  if key not in {"workspace_id", "effect_scope"}}
        ignored = {"observation_argv", "timeout"} | (set() if logical else {"id"})
        effect["action"] = {key: value for key, value in action.items() if key not in ignored}
        return hash_value(effect)

    @staticmethod
    def _scope(scope):
        """Freeze declared resources independently of executable recipe text."""
        resources = scope.get("resources") if isinstance(scope, dict) else None
        if (not isinstance(resources, list) or not resources
                or any(not isinstance(item, str) or not item.strip() for item in resources)):
            return None
        paths = [item.replace("\\", "/") for item in resources]
        # Device paths, drive-relative paths and trailing Windows dots/spaces
        # cannot establish a target distinct from an ordinary resource label.
        if any(item.startswith(("//./", "//?/"))
               or (len(item) > 2 and item[1] == ":" and item[2] != "/")
               or any(part not in {".", ".."} and part.endswith((".", " "))
                      for part in item.split("/")) for item in paths):
            return None
        # Only explicit Windows filesystem paths establish case-insensitive
        # identity. URI paths and opaque IDs retain their declared spelling.
        identities = []
        for original, path in zip(resources, paths):
            windows = ((len(path) > 2 and path[0].isalpha() and path[1:3] == ":/")
                       or original.startswith("\\\\"))
            identities.append(posixpath.normpath(path).casefold() if windows else original)
        return {"resources": sorted(set(identities))}

    @classmethod
    def _same_scope(cls, value, intent):
        # Known application permanently forbids replay. Legacy receipts or
        # callers without a footprint may still observe that same identity;
        # this never learns, removes or persists a scope from the declaration.
        if cls._history(value)[1] and ("effect_scope" not in value or "effect_scope" not in intent):
            return True
        # Preserve unscoped legacy exact aliases, but never let a new declaration
        # stand in for an unknown historic target, even with identical argv.
        if "effect_scope" not in value and "effect_scope" not in intent:
            return True
        previous = cls._scope(value.get("effect_scope"))
        current = cls._scope(intent.get("effect_scope"))
        return previous is not None and previous == current

    @staticmethod
    def _scopes_overlap(previous, current):
        def resources(scope):
            values = scope["resources"] if scope else []
            if (not values or any(item in {".", "..", "/", "//"} or item.startswith("../")
                    or (len(item) == 2 and item[1] == ":")
                    or any(char in item for char in "*?[]") for item in values)):
                return None
            comparable = []
            for item in values:
                if "://" in item:
                    try:
                        uri = urlsplit(item)
                    except ValueError:
                        return None
                    if not uri.scheme or not uri.netloc or "\\" in item:
                        return None
                    # Broad comparison can hold uncertain work, never prove
                    # identity. Normalize URI path separately from its syntax.
                    comparable.append(urlunsplit((uri.scheme.casefold(), uri.netloc.casefold(),
                        posixpath.normpath(uri.path).casefold(), "", "")))
                else:
                    path = item.replace("\\", "/")
                    windows = len(path) > 2 and path[0].isalpha() and path[1:3] == ":/"
                    if ":/" in path and not windows:
                        # Older normalization could have collapsed a URI's //.
                        # Its damaged spelling cannot establish disjointness.
                        return None
                    comparable.append(posixpath.normpath(path).casefold())
            return comparable

        left, right = resources(previous), resources(current)
        if left is None or right is None:
            return True
        def absolute(item):
            return item.startswith("/") or (len(item) > 2 and item[1:3] == ":/")

        # A relative declaration can name the same target as an absolute one;
        # executable text supplies no proof of its base directory.
        if any(absolute(a) != absolute(b) for a in left for b in right):
            return True
        return any(a == b or a.startswith(b.rstrip("/") + "/")
                   or b.startswith(a.rstrip("/") + "/") for a in left for b in right)

    def lookup(self, task_id, stage_id, action_id, intent):
        """Find the canonical same-root logical effect, without preparing it.

        Renaming stages/delegating cannot erase an uncertain attempt. Distinct
        logical actions may intentionally repeat a recipe after its prior effect
        is known. Changed command text does not prove a new effect: unresolved
        overlapping or unknown declared resources must first be reconciled.
        """
        identifier(stage_id, "stage id")
        identifier(action_id, "action id")
        if not isinstance(intent, dict) or not intent:
            raise ValueError("Operation requires explicit intent")
        root_id = self.root_task_id(task_id)
        identity = self._effect_identity(intent)
        recipe = self._effect_identity(intent, logical=False)
        scope = self._scope(intent.get("effect_scope"))
        exact = read_record(self.store.db, "operation", action_id)["payload"]
        if exact and (self.root_task_id(exact["task_id"]) != root_id
                      or self._effect_identity(exact["intent"]) != identity
                      or not self._same_scope(exact, intent)):
            raise ValueError("CONFLICT: action ID refers to different intent")
        matches = []
        for row in self.store.db.execute("SELECT payload FROM runtime_records WHERE kind='operation'"):
            value = json.loads(row["payload"])
            if self.root_task_id(value["task_id"]) != root_id:
                continue
            same_scope = self._same_scope(value, intent)
            if (self._effect_identity(value["intent"]) == identity
                    and same_scope):
                matches.append(value)
            else:
                action, previous_action = intent.get("action"), value["intent"].get("action")
                overlaps = self._scopes_overlap(self._scope(value.get("effect_scope")), scope)
                if (overlaps and isinstance(action, dict) and isinstance(previous_action, dict)
                        and "id" in action and action["id"] == previous_action.get("id")):
                    raise ValueError("CONFLICT: logical action ID refers to different intent")
                attempted, applied = self._history(value)
                unresolved = value["state"] == "UNKNOWN" or (attempted and not applied
                    and not self._confirmed_current_attempt(value))
                if unresolved and overlaps:
                    if self._effect_identity(value["intent"], logical=False) == recipe:
                        raise ValueError("CONFLICT: same recipe has an unresolved external effect")
                    if isinstance(intent.get("action"), dict) and isinstance(value["intent"].get("action"), dict):
                        raise ValueError("CONFLICT: declared resources have an unresolved external effect")
        if len(matches) > 1:
            raise ValueError("CONFLICT: multiple historical operations require reconciliation")
        return {**matches[0], "root_task_id": root_id} if matches else None

    def prepare(self, task_id, stage_id, action_id, intent):
        with transaction(self.store.db) as db:
            canonical = self.lookup(task_id, stage_id, action_id, intent)
            if canonical:
                return {**canonical, "new": False}
            value = {"action_id": action_id, "task_id": task_id, "stage_id": stage_id,
                     "intent": intent, "intent_hash": hash_value(intent), "state": "PREPARED",
                     "ever_attempted": False, "ever_applied": False,
                     "root_task_id": self.root_task_id(task_id)}
            if isinstance(intent.get("action"), dict) and "effect_scope" in intent:
                value["effect_scope"] = self._scope(intent["effect_scope"])
            write_record(db, "operation", action_id, value, expected_revision=0)
            append_event(db, "operation:" + action_id, "operation_prepared", task_id, value)
        return {**value, "new": True}

    def started(self, action_id):
        with transaction(self.store.db) as db:
            record = read_record(db, "operation", action_id)
            if not record["payload"]:
                raise ValueError("Unknown operation")
            previous = record["payload"]
            attempted, applied = self._history(previous)
            if (previous["state"] not in {"PREPARED", "ABSENT"} or applied
                    or (attempted and not self._confirmed_current_attempt(previous))):
                raise ValueError("Unresolved or applied operation cannot be replayed: " + previous["state"])
            value = dict(previous, state="UNKNOWN", ever_attempted=True, ever_applied=applied,
                         attempt_number=previous.get("attempt_number", 0) + 1)
            # A terminal rejection covers the previous attempt, never the next one.
            binding = value.pop("submission_binding", None)
            if binding:
                value["consumed_provider_attempt_ids"] = list(dict.fromkeys(
                    [*value.get("consumed_provider_attempt_ids", []), binding["provider_attempt_id"]]))
            self._clear_nonapplication(value)
            write_record(db, "operation", action_id, value, expected_revision=record["revision"])
        return value

    @staticmethod
    def _provider_attempt(value):
        text(value, "Provider attempt ID")
        if "[REDACTED]" in value:
            raise ValueError("Provider attempt ID must be its actual nonsecret identity")
        return value

    @staticmethod
    def _clear_nonapplication(value):
        for field in ("nonapplication_proof_ref", "nonapplication_proof_refs",
                      "nonapplication_provider_attempt_id", "nonapplication_attempt_number"):
            value.pop(field, None)

    @staticmethod
    def _current_binding(value):
        binding = value.get("submission_binding", {})
        return (isinstance(binding, dict) and type(value.get("attempt_number")) is int
                and value["attempt_number"] > 0
                and binding.get("attempt_number") == value["attempt_number"]
                and isinstance(binding.get("provider_attempt_id"), str)
                and bool(binding["provider_attempt_id"])
                and binding["provider_attempt_id"] not in value.get("consumed_provider_attempt_ids", []))

    @classmethod
    def _confirmed_current_attempt(cls, value):
        return (cls._current_binding(value) and bool(value.get("nonapplication_proof_ref"))
                and value.get("nonapplication_attempt_number") == value["attempt_number"]
                and value.get("nonapplication_provider_attempt_id") == value["submission_binding"]["provider_attempt_id"])

    def record_submission(self, action_id, execution):
        """Bind an actual submission receipt to the current local attempt.

        A recipe may emit one complete JSON object containing its provider's
        nonsecret ``provider_attempt_id``. This must come from the submission
        command's hashed output, not from a later observation or a local nonce.
        Missing/unstructured output supplies no binding and stays conservative.
        A provider identity cannot be reused for a different submission attempt.
        """
        if not isinstance(execution, dict):
            raise ValueError("Submission requires an execution receipt")
        text(execution.get("log"), "Submission log")
        text(execution.get("log_sha256"), "Submission log hash")
        try:
            log = Path(execution["log"])
            # Large ordinary command output can still reconcile positive effects;
            # it is not a supported structured provider acknowledgement.
            data = log.read_bytes() if log.stat().st_size <= 1024 * 1024 else None
        except OSError:
            raise ValueError("Submission log is unavailable") from None
        if data is not None and hashlib.sha256(data).hexdigest() != execution["log_sha256"]:
            raise ValueError("Submission log does not match its execution receipt")
        try:
            payload = json.loads(data.decode("utf-8")) if data is not None else None
        except (ValueError, UnicodeError):
            payload = None
        provider = payload.get("provider_attempt_id") if isinstance(payload, dict) else None
        if provider is not None:
            self._provider_attempt(provider)
        with transaction(self.store.db) as db:
            record = read_record(db, "operation", action_id)
            previous = record["payload"]
            if not previous:
                raise ValueError("Unknown operation")
            if (previous["state"] != "UNKNOWN" or type(previous.get("attempt_number")) is not int
                    or previous["attempt_number"] <= 0):
                raise ValueError("Submission has no current started attempt")
            if provider is None:
                return previous
            if provider in previous.get("consumed_provider_attempt_ids", []):
                raise ValueError("Provider attempt ID was consumed by an earlier submission")
            binding = {"attempt_number": previous["attempt_number"], "provider_attempt_id": provider,
                       "proof_ref": execution["log"] + "#sha256=" + execution["log_sha256"]}
            current = previous.get("submission_binding")
            if current:
                if current["provider_attempt_id"] != provider:
                    raise ValueError("Submission conflicts with current provider attempt binding")
                return previous
            value = dict(previous, submission_binding=binding)
            write_record(db, "operation", action_id, value, expected_revision=record["revision"])
            append_event(db, "operation:" + action_id + ":submission:" + str(previous["attempt_number"]),
                         "operation_submission_recorded", value["task_id"], value)
        return value

    @staticmethod
    def _history(value):
        state = value["state"]
        # Old ABSENT records did not retain whether an effect had been attempted.
        attempted = (bool(value.get("ever_attempted")) or state in {"UNKNOWN", "APPLIED"}
                     or (state == "ABSENT" and "ever_attempted" not in value))
        applied = bool(value.get("ever_applied")) or state == "APPLIED"
        return attempted, applied

    def reconcile(self, action_id, observation):
        """Record a probe; nonvisibility alone never disproves an attempted effect.

        ``absence_confirmed`` asserts terminal nonapplication. After a submission
        it also needs the same provider attempt identity captured from that
        submission's receipt. Fresh log filenames cannot renew an old rejection.
        """
        if not isinstance(observation, dict) or type(observation.get("exists")) is not bool:
            raise ValueError("Reconciliation requires an actual explicit status observation and proof_ref")
        text(observation.get("proof_ref"), "Reconciliation proof_ref")
        if "absence_confirmed" in observation and type(observation["absence_confirmed"]) is not bool:
            raise ValueError("absence_confirmed must be a boolean")
        confirmed = observation.get("absence_confirmed", False)
        provider = observation.get("provider_attempt_id")
        if "provider_attempt_id" in observation:
            self._provider_attempt(provider)
        if observation["exists"]:
            text(observation.get("resource_id"), "Existing effect resource ID")
            if "[REDACTED]" in observation["resource_id"]:
                raise ValueError("Existing effect requires its actual resource ID")
            if confirmed:
                raise ValueError("An existing effect contradicts confirmed nonapplication")
        with transaction(self.store.db) as db:
            record = read_record(db, "operation", action_id)
            if not record["payload"]:
                raise ValueError("Unknown operation")
            previous = record["payload"]
            attempted, applied = self._history(previous)
            if confirmed and applied:
                raise ValueError("Confirmed nonapplication contradicts previously observed application")
            bound_terminal = (confirmed and self._current_binding(previous)
                              and provider == previous["submission_binding"]["provider_attempt_id"])
            if observation["exists"]:
                state = "APPLIED"
            elif not applied and (not attempted or bound_terminal
                    or (not confirmed and self._confirmed_current_attempt(previous))):
                state = "ABSENT"
            else:
                state = "UNKNOWN"
            value = dict(previous, state=state, observation=observation,
                         ever_attempted=attempted, ever_applied=applied or observation["exists"])
            if bound_terminal:
                value["nonapplication_proof_ref"] = observation["proof_ref"]
                value["nonapplication_provider_attempt_id"] = provider
                value["nonapplication_attempt_number"] = value["attempt_number"]
            elif state != "ABSENT":
                self._clear_nonapplication(value)
            write_record(db, "operation", action_id, value, expected_revision=record["revision"])
            append_event(db, "operation:" + action_id + ":observation:" + str(record["revision"] + 1),
                         "operation_reconciled", value["task_id"], value)
        return value
