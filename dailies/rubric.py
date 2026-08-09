"""Review rubrics: the extensibility story.

A rubric is a set of named rules, each a prompt for the VLM plus an optional
fail_at severity. Users add project-specific rules (a prop's continuity, a
wardrobe color) with zero code. JSON always works; YAML works when PyYAML
is installed, so the repo stays dependency-free.

Rules that need context from the recipe declare it with "needs" (dotted path
into take.json, e.g. "recipe.prompt_text") and reference it in the prompt as
{prompt}. Such rules are skipped for takes that lack the context.
"""

import json

DEFAULT = {
    "anatomy.hands": {
        "prompt": "Examine every visible hand. Count fingers, check joint "
                  "bends and grips. Report frames where anatomy is wrong.",
        "fail_at": 4,
    },
    "anatomy.faces": {
        "prompt": "Examine every visible face for warped, melted, or "
                  "asymmetric features, and eyes looking in impossible "
                  "directions.",
        "fail_at": 4,
    },
    "artifact.morphing": {
        "prompt": "Look for objects or body parts that morph, dissolve, "
                  "duplicate, or teleport between frames.",
        "fail_at": 4,
    },
    "anatomy.limbs": {
        "prompt": "Check every visible body: number of arms and legs, "
                  "joint bends, and poses. Report extra or missing limbs "
                  "and impossible articulation.",
        "fail_at": 4,
    },
    "physics.contact": {
        "prompt": "Do held or contacted objects move rigidly with the hand "
                  "or surface touching them? Report slipping, clipping, or "
                  "floating contact.",
        "fail_at": 4,
    },
    "physics.motion": {
        "prompt": "Does movement obey gravity and momentum? Report objects "
                  "or people that glide, accelerate impossibly, or move "
                  "without a driving force.",
        "fail_at": 4,
    },
    "continuity.objects": {
        "prompt": "Count the important objects (props, items being "
                  "handled) in each frame. Report objects that vanish or "
                  "appear between frames without leaving or entering the "
                  "frame naturally. An object that disappears entirely is "
                  "severity 4 or 5.",
        "fail_at": 4,
    },
    "environment.stability": {
        "prompt": "Watch the background and setting. Report walls, "
                  "furniture, or scenery that melt, warp, or rearrange "
                  "between frames.",
        "fail_at": 4,
    },
    "text.legibility": {
        "prompt": "Find any text in the frames: signs, labels, screens, "
                  "clothing. Report text that is garbled, misspelled, or "
                  "changes between frames.",
        "fail_at": 4,
    },
    "adherence.prompt": {
        "needs": "recipe.prompt_text",
        "prompt": "The direction for this shot was: {prompt}\n"
                  "Judge how well these frames follow it. Report each "
                  "missed element as a defect, severity by importance.",
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
        if "prompt" not in rule:
            raise ValueError("rubric rule %r has no prompt" % name)
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
