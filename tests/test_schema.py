"""Published schemas validated against synthesized dailies output.

Stdlib only, so validation is a small JSON Schema subset interpreter
covering exactly what the checked-in schemas use: type, properties,
required, items, enum, pattern, anyOf, additionalProperties-as-schema.
A schema drifting outside that subset should extend the interpreter,
not silently pass.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dailies import calibrate, judgecheck, take  # noqa: E402
from dailies.cli import main  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
SCHEMAS = os.path.join(os.path.dirname(__file__), "..", "dailies",
                       "schemas")
KNOWN_KEYWORDS = {"$schema", "$id", "title", "description", "type",
                  "properties", "required", "items", "enum", "pattern",
                  "anyOf", "additionalProperties"}

_TYPES = {
    "object": lambda d: isinstance(d, dict),
    "array": lambda d: isinstance(d, list),
    "string": lambda d: isinstance(d, str),
    "integer": lambda d: isinstance(d, int)
    and not isinstance(d, bool),
    "number": lambda d: isinstance(d, (int, float))
    and not isinstance(d, bool),
    "boolean": lambda d: isinstance(d, bool),
    "null": lambda d: d is None,
}


def errors(schema, doc, path="$"):
    """All violations of the schema subset; empty list means valid."""
    errs = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](doc) for x in types):
            return ["%s: expected %s, got %r" % (path, types, doc)]
    if "enum" in schema and doc not in schema["enum"]:
        errs.append("%s: %r not in %s" % (path, doc, schema["enum"]))
    if "pattern" in schema and isinstance(doc, str):
        if not re.search(schema["pattern"], doc):
            errs.append("%s: %r fails pattern %s"
                        % (path, doc, schema["pattern"]))
    if "anyOf" in schema:
        branches = [errors(s, doc, path) for s in schema["anyOf"]]
        if all(branches):
            errs.append("%s: no anyOf branch matched" % path)
    if isinstance(doc, dict):
        for key in schema.get("required", []):
            if key not in doc:
                errs.append("%s: missing required key %r" % (path, key))
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in doc:
                errs.extend(errors(sub, doc[key],
                                   "%s.%s" % (path, key)))
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            for key in doc:
                if key not in props:
                    errs.extend(errors(extra, doc[key],
                                       "%s.%s" % (path, key)))
    if isinstance(doc, list) and "items" in schema:
        for i, item in enumerate(doc):
            errs.extend(errors(schema["items"], item,
                               "%s[%d]" % (path, i)))
    return errs


def load_schema(name):
    with open(os.path.join(SCHEMAS, name + ".schema.json")) as f:
        return json.load(f)


def keywords(schema):
    """Schema keywords used anywhere in a document, skipping property
    names, which are data."""
    seen = set(schema)
    for key, val in schema.items():
        if key == "properties":
            for sub in val.values():
                seen |= keywords(sub)
        elif key in ("items", "additionalProperties") \
                and isinstance(val, dict):
            seen |= keywords(val)
        elif key == "anyOf":
            for sub in val:
                seen |= keywords(sub)
    return seen


def gold_sidecar(dirpath, name, label, severities, rule="r.x",
                 mech_kill=False):
    """Fabricated gold-labeled, reviewed sidecar plus a stub clip file
    so judge-check finds the clip on disk. severities of None means no
    VLM block, the state judge-check judges from mechanics alone."""
    if severities is None:
        vlm = None
    else:
        vlm = {"engine": "stub",
               "defects": [{"t": 1.0, "severity": s, "note": "x",
                            "rule": rule} for s in severities]}
    reasons = ["black for 1.0s of 1.0s"] if mech_kill else []
    t = {"take_id": "sha256:%064x" % 0, "shot": "s", "parent": None,
         "created": "2026-08-09T00:00:00Z",
         "output": {"file": name}, "recipe": None,
         "gold": {"label": label, "labeled": "2026-08-09T00:00:00Z"},
         "review": {"mechanical": {"kill_reasons": reasons},
                    "vlm": vlm,
                    "verdict": "kill" if mech_kill else "review",
                    "rank_in_shot": 1}}
    open(os.path.join(dirpath, name), "wb").close()
    with open(os.path.join(dirpath, name + ".take.json"), "w") as f:
        json.dump(t, f)
    return t


class ValidatorTests(unittest.TestCase):
    """The interpreter has teeth before anything trusts it."""

    def test_type_required_enum_pattern_items(self):
        schema = load_schema("take")
        self.assertTrue(errors(schema, {"shot": "s"}))  # no output
        self.assertTrue(errors(schema, {"output": {"file": 1}}))
        bad_verdict = {"output": {"file": "a.mp4"},
                       "review": {"mechanical": {"kill_reasons": []},
                                  "verdict": "maybe"}}
        self.assertTrue(errors(schema, bad_verdict))
        bad_hash = {"output": {"file": "a.mp4"}, "take_id": "nope"}
        self.assertTrue(errors(schema, bad_hash))
        bad_reason = {"output": {"file": "a.mp4"},
                      "review": {"mechanical": {"kill_reasons": [3]},
                                 "verdict": "kill"}}
        self.assertTrue(errors(schema, bad_reason))

    def test_booleans_are_not_numbers(self):
        self.assertTrue(errors({"type": "integer"}, True))
        self.assertFalse(errors({"type": "number"}, 3))

    def test_anyof_and_typed_additional_properties(self):
        schema = load_schema("calibration")
        self.assertTrue(errors(schema, {"n_pass": 5}))
        self.assertTrue(errors(schema, {"alpha": 0.05, "lambda": 1.0,
                                        "weights": {"r.x": "high"},
                                        "bias": 0.0}))

    def test_schemas_stay_inside_the_subset(self):
        for name in ("take", "calibration", "judge-history"):
            unknown = keywords(load_schema(name)) - KNOWN_KEYWORDS
            self.assertFalse(unknown, "%s uses unvalidated keywords %s"
                             % (name, unknown))


class SchemaCommandTests(unittest.TestCase):
    def test_prints_the_checked_in_file_verbatim(self):
        for name in ("take", "calibration", "judge-history"):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(["schema", name])
            self.assertEqual(rc, 0, name)
            path = os.path.join(SCHEMAS, name + ".schema.json")
            with open(path) as f:
                self.assertEqual(out.getvalue(), f.read(), name)
            json.loads(out.getvalue())


class SidecarSchemaTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-schema-test-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.schema = load_schema("take")

    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_reviewed_sidecar_validates(self):
        clip = os.path.join(self.dir, "take.mp4")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=320x240:rate=8", "-t", "1",
             "-pix_fmt", "yuv420p", clip],
            check=True)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["review", clip]), 0)
        self.assertEqual(errors(self.schema, take.load(clip)), [])

    def test_spec_style_sidecar_with_recipe_and_gold_validates(self):
        t = gold_sidecar(self.dir, "a.mp4", "pass", [3, 5])
        t["recipe"] = {"workflow": {}, "seeds": {"3": 424242},
                       "prompt_text": "slow dolly"}
        t["review"]["vlm"]["defects"][0].update(
            {"t_end": 4.1, "confidence": 0.67})
        t["review"]["vlm"].update(
            {"samples": 3, "skipped": [], "uncertain": [],
             "escalated": ["r.x"], "strong_engine": "big-vlm"})
        self.assertEqual(errors(self.schema, t), [])

    def test_unknown_keys_are_tolerated(self):
        # SPEC law: unknown keys are preserved, so the schema must not
        # reject a sidecar carrying another tool's block.
        t = gold_sidecar(self.dir, "b.mp4", "kill", [4])
        t["slate"] = {"anything": True}
        t["review"]["costs"] = {"usd": 0.01}
        self.assertEqual(errors(self.schema, t), [])


class CalibrationSchemaTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dailies-calschema-test-")
        self.addCleanup(shutil.rmtree, self.dir)
        for i, sevs in enumerate([[], [], [1], [2], [4]]):
            gold_sidecar(self.dir, "p%d.mp4" % i, "pass", sevs)
        for i in range(4):
            gold_sidecar(self.dir, "k%d.mp4" % i, "kill", [4],
                         rule="a.kills")

    def test_calibrate_fit_and_merged_output_validate(self):
        schema = load_schema("calibration")
        cal = calibrate.calibrate(self.dir, alpha=0.5)
        self.assertEqual(errors(schema, cal), [])
        fitted = calibrate.fit(self.dir)
        self.assertEqual(errors(schema, fitted), [])
        cal.update(fitted)  # cmd_fit merges into one file
        self.assertEqual(errors(schema, cal), [])

    def test_refused_guarantee_still_validates(self):
        schema = load_schema("calibration")
        cal = calibrate.calibrate(self.dir, alpha=0.05)
        self.assertIsNone(cal["lambda"])
        self.assertEqual(errors(schema, cal), [])


class JudgeHistorySchemaTests(unittest.TestCase):
    def test_history_written_by_judgecheck_validates(self):
        # Mechanical kills need no judge, so the record is synthesized
        # end to end without a VLM endpoint.
        root = tempfile.mkdtemp(prefix="dailies-jcschema-test-")
        self.addCleanup(shutil.rmtree, root)
        for i in range(3):
            gold_sidecar(root, "k%d.mp4" % i, "kill", None,
                         mech_kill=True)
        history = os.path.join(root, "history.json")
        for _ in range(2):
            judgecheck.run(root, {}, history_path=history,
                           vlm_endpoint=None, vlm_model="stub",
                           samples=2)
        with open(history) as f:
            doc = json.load(f)
        self.assertEqual(len(doc["runs"]), 2)
        self.assertEqual(errors(load_schema("judge-history"), doc), [])


if __name__ == "__main__":
    unittest.main()
