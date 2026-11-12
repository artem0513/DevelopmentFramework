"""Durable intent before effects; a lost acknowledgement remains unknown."""
from .state import append_event, hash_value, identifier, read_record, transaction, write_record


class OperationLedger:
    def __init__(self, store):
        self.store = store

    def prepare(self, task_id, stage_id, action_id, intent):
        self.store.task(task_id)
        identifier(stage_id, "stage id")
        identifier(action_id, "action id")
        if not isinstance(intent, dict) or not intent:
            raise ValueError("Operation requires explicit intent")
        with transaction(self.store.db) as db:
            record = read_record(db, "operation", action_id)
            if record["payload"]:
                value = record["payload"]
                if (value["task_id"], value["stage_id"], value["intent_hash"]) != (task_id, stage_id, hash_value(intent)):
                    raise ValueError("CONFLICT: action ID refers to different intent")
                return {**value, "new": False}
            value = {"action_id": action_id, "task_id": task_id, "stage_id": stage_id,
                     "intent": intent, "intent_hash": hash_value(intent), "state": "PREPARED"}
            write_record(db, "operation", action_id, value, expected_revision=0)
            append_event(db, "operation:" + action_id, "operation_prepared", task_id, value)
        return {**value, "new": True}

    def started(self, action_id):
        with transaction(self.store.db) as db:
            record = read_record(db, "operation", action_id)
            if not record["payload"]:
                raise ValueError("Unknown operation")
            value = dict(record["payload"], state="UNKNOWN")
            write_record(db, "operation", action_id, value, expected_revision=record["revision"])
        return value

    def reconcile(self, action_id, observation):
        if (not isinstance(observation, dict) or type(observation.get("exists")) is not bool
                or not isinstance(observation.get("proof_ref"), str) or not observation["proof_ref"]):
            raise ValueError("Reconciliation requires an actual explicit status observation and proof_ref")
        if observation["exists"] and not observation.get("resource_id"):
            raise ValueError("Existing effect requires its actual resource ID")
        with transaction(self.store.db) as db:
            record = read_record(db, "operation", action_id)
            if not record["payload"]:
                raise ValueError("Unknown operation")
            value = dict(record["payload"], state="APPLIED" if observation["exists"] else "ABSENT", observation=observation)
            write_record(db, "operation", action_id, value, expected_revision=record["revision"])
            append_event(db, "operation:" + action_id + ":observation:" + str(record["revision"] + 1),
                         "operation_reconciled", value["task_id"], value)
        return value
