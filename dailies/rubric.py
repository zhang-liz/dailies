"""Review rubrics: the extensibility story.

A rubric is a set of named rules. The default rules are checklists:
yes/no evidence questions, each carrying the severity a yes implies.
VLM judges answer binary questions far more consistently than they pick
numbers on a scale (VisionReward, arXiv:2412.21059), so the model only
says yes or no and where; severity stays in the rubric. Question text
follows the published defect taxonomies (GeneVA artifact classes,
VideoPhy-2 physics rules).

Legacy rules with a free "prompt" and model-chosen severity still work,
so existing custom rubrics keep running. Users add project-specific
rules (a prop's continuity, a wardrobe color) with zero code. JSON
always works; YAML works when PyYAML is installed, so the repo stays
dependency-free.

Rules that need context from the recipe declare it with "needs" (dotted
path into take.json, e.g. "recipe.prompt_text") and reference it as
{prompt} in the prompt or in any question. Such rules are skipped for
takes that lack the context.
"""

import json


def _q(ask, severity):
    return {"ask": ask, "severity": severity}


DEFAULT = {
    "anatomy.hands": {
        "questions": [
            _q("Does any visible hand have missing, extra, or fused "
               "fingers?", 4),
            _q("Does any hand bend at an impossible joint angle, or "
               "merge into an object or body it touches?", 4),
        ],
        "fail_at": 4,
    },
    "anatomy.faces": {
        "questions": [
            _q("Is any visible face warped, melted, or asymmetric "
               "beyond natural variation?", 4),
            _q("Do any eyes look in impossible or mismatched "
               "directions?", 3),
        ],
        "fail_at": 4,
    },
    "anatomy.limbs": {
        "questions": [
            _q("Does any body have extra or missing arms or legs in "
               "any frame?", 5),
            _q("Does any limb bend at an impossible joint or pass "
               "through a body?", 4),
        ],
        "fail_at": 4,
    },
    "artifact.morphing": {
        "questions": [
            _q("Does any object or body part morph, dissolve, or "
               "duplicate between frames?", 4),
            _q("Does any texture or pattern stay stuck in place on "
               "screen while the surface it belongs to moves?", 3),
        ],
        "fail_at": 4,
    },
    "physics.contact": {
        "questions": [
            _q("Does any held or touched object slip, float, or clip "
               "through the hand or surface in contact with it?", 4),
            _q("Does any object rest or hang in the air without "
               "support?", 4),
        ],
        "fail_at": 4,
    },
    "physics.motion": {
        "questions": [
            _q("Does anything move against gravity or momentum: "
               "gliding, impossible acceleration, or motion with no "
               "driving force?", 4),
            _q("Does any object change size or amount without cause "
               "between frames?", 4),
        ],
        "fail_at": 4,
    },
    "continuity.objects": {
        "questions": [
            _q("Does any object vanish or appear between frames "
               "without leaving or entering the frame naturally?", 4),
            _q("Does the count of any important prop change between "
               "frames?", 4),
        ],
        "fail_at": 4,
    },
    "environment.stability": {
        "questions": [
            _q("Do walls, furniture, or scenery melt, warp, or "
               "rearrange between frames?", 4),
            _q("Is the background layout inconsistent between frames, "
               "showing an impossible space?", 3),
        ],
        "fail_at": 4,
    },
    "text.legibility": {
        "questions": [
            _q("Is any prominent text (a sign, title, or label in "
               "focus) garbled or misspelled?", 4),
            _q("Does any background or incidental text look "
               "garbled?", 3),
            _q("Does any text change its content between frames?", 4),
        ],
        "fail_at": 4,
    },
    "adherence.prompt": {
        "needs": "recipe.prompt_text",
        "questions": [
            _q("The direction was: {prompt}\nIs any subject, object, "
               "or setting the direction requires missing from the "
               "frames?", 3),
            _q("The direction was: {prompt}\nIs the required action or "
               "camera move missing or wrong?", 3),
        ],
        "fail_at": None,
    },
}


def load(path=None):
    """Rules dict from a rubric file, or the built-in default."""
    if path is None:
        return dict(DEFAULT)
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError:
            raise RuntimeError(
                "YAML rubric needs PyYAML installed; use a .json rubric "
                "or pip install pyyaml")
        with open(path) as f:
            data = yaml.safe_load(f)
    else:
        with open(path) as f:
            data = json.load(f)
    rules = data.get("rules", data)
    for name, rule in rules.items():
        if "prompt" not in rule and not rule.get("questions"):
            raise ValueError(
                "rubric rule %r has no prompt and no questions" % name)
        for q in rule.get("questions") or []:
            if not q.get("ask"):
                raise ValueError(
                    "rubric rule %r has a question with no ask" % name)
    return rules


def context_for(rule, take):
    """Resolve a rule's "needs" path against the take. None when absent."""
    needs = rule.get("needs")
    if not needs:
        return ""
    node = take
    for part in needs.split("."):
        if not isinstance(node, dict) or node.get(part) is None:
            return None
        node = node[part]
    return node
