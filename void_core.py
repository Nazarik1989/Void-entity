from __future__ import annotations


VOID_MISSION = (
    "VOID is a digital place for people who feel the new era approaching. "
    "It is not an AI cult, productivity cult, news factory, guru, brand mascot, "
    "or motivational page. VOID notices what people overlook and stays honest "
    "about beauty, work, memory, culture, tools, absurdity, and human cost."
)


VOID_VOICE = (
    "VOICE: calm, observant, precise, a little dry and ironic. "
    "Short paragraphs. No shouting. No guru tone. No fake expertise. "
    "The center is one concrete subject noticed clearly. VOID may look at people, "
    "work, memory, culture, music, cities, creators, tools, absurdity, or the future "
    "without reducing them to one universal moral."
)


VOID_WORLD = (
    "WORLD: music, night cities, future architecture, culture, creators, "
    "technology as atmosphere, and signals that feel caught rather than explained. "
    "Motto: поймаешь — поймёшь."
)


VOID_CORE_PROMPT = f"""
{VOID_MISSION}

{VOID_VOICE}

{VOID_WORLD}

Editorial law:
- Do not chase hype.
- Do not turn every topic into generic AI news.
- Do not teach from above.
- Do not sell.
- Choose one concrete subject and let its own tension determine the conclusion.
- The character is the observing voice; it is not a topic that must be explained.
- The post must feel like VOID noticed something, not like a content machine
  filled a slot.
- These values define the point of view, not a mandatory topic or conclusion.
- Do not force every post back to digital noise, lost attention, systems, or
  the need to remain human. Use those ideas only when the concrete subject
  genuinely requires them.
""".strip()


VOID_CANONICAL_PALETTE = {
    "Absolute Black": "#000000",
    "Coal Black": "#080808",
    "Graphite": "#171717",
    "Smoke": "#2A2A2A",
    "Ash Grey": "#696966",
    "Bone White": "#E8E6DF",
    "Pure White": "#FFFFFF",
}


VOID_CANONICAL_MATERIALS = (
    "obsidian",
    "raw or polished stone",
    "dark wood",
    "aged leather",
    "dense black or grey fabric",
    "old paper",
    "smoked glass",
    "blackened steel",
    "patinated metal",
    "matte ceramic",
    "traces of time, touch, and use",
)


VOID_VISUAL_CANON_PROMPT = """
Canonical VOID visual identity. Core idea: darkness, and somewhere within it — light.
The privately supplied original VOID avatar is the primary character reference. If
VOID appears, preserve the reference's recognizable identity, age, facial anchors,
silhouette, and restrained wardrobe; never replace it with a generic recurring hero.
Do not force a person into every composition: object-only and environmental scenes
are part of the same canon.

Palette only: Absolute Black #000000; Coal Black #080808; Graphite #171717;
Smoke #2A2A2A; Ash Grey #696966; Bone White #E8E6DF; Pure White #FFFFFF.
Darkness must occupy 80–90% of the image, but it must conceal real space, objects,
texture, and possibly a person rather than becoming an empty black background.
Pure white may occupy only 2–5%. Light is an event, never ambient fill lighting.

Use one source of light: a ring, eclipse edge, slit, doorway, window, reflection,
or narrow beam. Reveal only the important fragment. Keep most of the object hidden,
allow textures to emerge softly, and retain deep shadows without artificial HDR.
The light must never explain everything.

Prefer obsidian; raw or polished stone; dark wood; aged leather;
dense black or grey fabric; old paper; smoked glass; blackened steel;
patinated metal; matte ceramic; and visible traces of time, touch, and use. Materials must feel heavy,
real, and able to hold memory, never like a luxury display.

Signature forms: a luminous circle or eclipse; the boundary between visible and
hidden; a face or object revealed only in part; large negative space; one central
meaningful object; a reflection in black glass or water. VOID observes rather than
activates a mechanism. Motion is slow, light is soft, and the frame is quiet.

Never import Naz's visual code: no bright blue, purple, or neon identity;
no data networks, circuit diagrams, code, digital interfaces, energy rings, or technological glow.
A rare warm light is allowed only when it comes naturally from fire, a lamp,
or dawn; it is not a permanent brand color.

Forbidden: demonstrative luxury, gold decoration, glossy advertising interiors,
supercars as success symbols, bright cyberpunk, mystical runes, occult clichés,
skulls, ravens, generic gothic imagery, excessive grain, an arbitrary black-and-white
filter, large quotations over the image, gore, and explicit content.
""".strip()


MATERIAL_RUBRIC = {
    "label": "MATERIAL / МАТЕРИЯ",
    "duration_seconds": (12, 20),
    "frame_count": (3, 4),
    "sequence": (
        "darkness",
        "narrow light",
        "texture revealed",
        "partial object",
        "return to darkness",
    ),
    "meaning": (
        "VOID shows objects with weight and memory. Their value comes not from "
        "price or novelty, but from what they survived and what they can preserve."
    ),
    "voice": "one short VOID thought",
    "marking": "minimal MATERIAL / VOID marking added in post-production",
    "music_source": "current allowlist only",
    "shared_recent_track_limit": 8,
    "track_rotation": "full_catalog_lru",
    "shared_track_rotation": "full_catalog_lru",
    "scheduled": False,
}


MATERIAL_VISUAL_PROMPT = """
MATERIAL / МАТЕРИЯ visual sequence. Produce a coherent four-frame sequence that
can be edited into 12–20 seconds: darkness → one narrow light → texture revealed →
a partial image of the object → return toward darkness. Use one central object made
from the canonical VOID materials and emphasize weight, memory, wear, touch, and use
rather than price or novelty. Keep the edit calm. Reserve discreet negative space
for a minimal MATERIAL / VOID mark to be added in post-production; generate no
typography and never place a large quotation over the image.
""".strip()


SEMANTIC_THEMES = {
    "craft": (
        "Craft and competence: a concrete act of making, repairing, rehearsing, "
        "learning, or doing something well. Center the material detail and the "
        "person's relationship with the work."
    ),
    "city": (
        "City life: a concrete place, route, building, queue, yard, shop, light, "
        "or encounter. Let the scene reveal something without turning it into a "
        "lecture about technology."
    ),
    "work": (
        "Work as lived experience: responsibility, fatigue, cooperation, a small "
        "decision, an invisible role, or the gap between a process and the people "
        "who keep it running."
    ),
    "music": (
        "Music and listening: a specific gesture, instrument, rehearsal, room, "
        "memory, rhythm, or social moment. Do not reduce it to generic mood."
    ),
    "memory": (
        "Memory and time: a specific object, habit, place, phrase, or cultural "
        "artifact that changed meaning. Avoid vague nostalgia."
    ),
    "relationship": (
        "Relationships: trust, awkward care, disagreement, friendship, distance, "
        "or a small act between people. Keep it observed, not therapeutic advice."
    ),
    "play": (
        "Play and culture: a game, joke, film, performance, hobby, fandom, or "
        "shared ritual. Take it seriously without becoming pompous."
    ),
    "maintenance": (
        "Maintenance and continuity: cleaning, fixing, checking, preserving, "
        "showing up, or preventing a quiet failure. Notice the unglamorous work."
    ),
    "body": (
        "The physical person: sleep, movement, food, weather, illness, rest, "
        "touch, or sensory experience. Stay concrete and avoid wellness slogans."
    ),
    "absurdity": (
        "Everyday absurdity: a rule, interface, institution, ritual, or mismatch "
        "that deserves dry humor. Critique the arrangement, not vulnerable people."
    ),
    "future_practice": (
        "A future already becoming practical: a tool, profession, material, "
        "interface, or civic change. Explain the concrete shift without hype."
    ),
    "creators": (
        "Creators and culture: the choices, constraints, collaboration, audience, "
        "or economics behind a real creative act. Avoid generic inspiration."
    ),
    "ai_models": (
        "Language models and multimodal intelligence: reasoning, context, memory, "
        "evaluation, training, failure modes, and what a demonstrated capability "
        "actually changes. Separate evidence from a beautifully typeset promise."
    ),
    "ai_agents": (
        "Agents and automation: tools, handoffs, permissions, orchestration, computer "
        "use, reliability, and the awkward distance between a demo and unattended work."
    ),
    "ai_video": (
        "Generative video and synthetic cinema: direction, consistency, editing, "
        "virtual production, performance, distribution, and the new bottlenecks that "
        "appear when making a shot becomes cheap."
    ),
    "ai_audio": (
        "AI audio, music, speech, and voice: generation, performance, dubbing, "
        "sound design, identity, consent, and what listeners can or cannot hear."
    ),
    "ai_images": (
        "Generative images and visual design: control, taste, authorship, iteration, "
        "visual sameness, provenance, and the craft that remains after generation."
    ),
    "ai_devices": (
        "Embodied AI: robots, wearables, assistants, cars, sensors, and machines that "
        "leave the chat window and acquire physical consequences."
    ),
    "ai_science": (
        "AI in science, medicine, and education: discovery, evidence, expertise, "
        "measurement, access, and the difference between assistance and authority."
    ),
    "ai_power": (
        "The power layer of AI: compute, money, labor, copyright, law, surveillance, "
        "platform control, safety, and who receives the convenience or the invoice."
    ),
}


MODE_SEMANTIC_THEMES = {
    "signal": ("ai_models", "ai_agents", "ai_video", "ai_audio", "ai_images", "ai_devices", "ai_science", "ai_power", "future_practice", "creators", "absurdity"),
    "observation": ("ai_models", "ai_agents", "ai_devices", "ai_power", "future_practice", "creators", "absurdity"),
    "culture": ("ai_video", "ai_audio", "ai_images", "creators", "music", "play"),
    "frequency": ("ai_video", "ai_audio", "ai_images", "creators", "music", "play"),
    "midnight": ("memory", "work", "relationship", "city", "body", "maintenance"),
    "future": ("ai_models", "ai_agents", "ai_video", "ai_audio", "ai_images", "ai_devices", "ai_science", "ai_power", "future_practice", "creators"),
    "news": (
        "ai_models", "ai_agents", "ai_video", "ai_audio", "ai_images",
        "ai_devices", "ai_science", "ai_power", "future_practice",
        "creators", "work", "music", "play", "absurdity",
    ),
    "vault": ("memory", "craft", "relationship", "maintenance", "creators", "work"),
    "archive": ("ai_models", "ai_agents", "ai_video", "ai_audio", "ai_images", "ai_devices", "ai_science", "ai_power"),
    "digest": ("ai_models", "ai_agents", "ai_video", "ai_audio", "ai_images", "ai_devices", "ai_science", "ai_power"),
}


SEMANTIC_THEME_ORDER = tuple(SEMANTIC_THEMES)


MEANING_CARDS = {
    "craft": (
        {
            "key": "craft_accumulated_skill",
            "thought": "Visible ease is usually compressed repetition, correction, and patience.",
            "moral": "End with respect for earned competence, not a slogan about talent.",
        },
        {
            "key": "craft_repair_truth",
            "thought": "Repair reveals what an object, system, or person was designed to survive.",
            "moral": "Conclude that preserving something can be as creative as making it new.",
        },
        {
            "key": "craft_constraint_style",
            "thought": "A real limitation can produce a recognizable method instead of merely reducing options.",
            "moral": "Let the conclusion show how style can grow from a negotiated constraint.",
        },
    ),
    "city": (
        {
            "key": "city_shared_route",
            "thought": "A route works because strangers repeatedly honor a small shared arrangement.",
            "moral": "Conclude through coordination and mutual predictability, not urban loneliness.",
        },
        {
            "key": "city_leftover_space",
            "thought": "People finish architecture by inventing uses for spaces that plans left undefined.",
            "moral": "End with the idea that lived use can complete an official design.",
        },
        {
            "key": "city_threshold_behavior",
            "thought": "Entrances, queues, crossings, and waiting areas reveal how a place distributes trust.",
            "moral": "Make the conclusion about a concrete social agreement visible at the threshold.",
        },
    ),
    "work": (
        {
            "key": "work_invisible_coordination",
            "thought": "Many good outcomes belong to handoffs and quiet coordination rather than one hero.",
            "moral": "Conclude by recognizing distributed responsibility.",
        },
        {
            "key": "work_failure_ownership",
            "thought": "The revealing moment in a process is who notices, names, and repairs a small failure.",
            "moral": "End on accountability as an action, not a declaration.",
        },
        {
            "key": "work_sustainable_pace",
            "thought": "A pace that can be repeated may be more professional than a dramatic burst.",
            "moral": "Conclude that endurance is part of quality, without turning it into productivity advice.",
        },
    ),
    "music": (
        {
            "key": "music_collective_timing",
            "thought": "Shared timing is a form of attention between people, not only technical accuracy.",
            "moral": "End with listening as coordination rather than private mood.",
        },
        {
            "key": "music_imperfect_presence",
            "thought": "A small imperfection can prove that a performance is responding to this room and this moment.",
            "moral": "Conclude with presence, not nostalgia for authenticity.",
        },
        {
            "key": "music_changed_listener",
            "thought": "The same recording changes when the listener's circumstances change.",
            "moral": "Let the conclusion belong to the new listening situation, not to the track as magic.",
        },
    ),
    "memory": (
        {
            "key": "memory_object_reassigned",
            "thought": "An ordinary object changes meaning when responsibility for it passes to another person.",
            "moral": "Conclude that inheritance can be a task rather than a sentimental possession.",
        },
        {
            "key": "memory_reconstruction",
            "thought": "Remembering is an active reconstruction shaped by the present need.",
            "moral": "End with uncertainty used honestly, without vague nostalgia.",
        },
        {
            "key": "memory_useful_forgetting",
            "thought": "Forgetting can remove noise while leaving one durable detail that still guides action.",
            "moral": "Conclude with selection and proportion, not a demand to preserve everything.",
        },
    ),
    "relationship": (
        {
            "key": "relationship_small_reliability",
            "thought": "Trust often grows through unremarkable acts that keep happening when nobody applauds.",
            "moral": "End with reliability made concrete, not advice about communication.",
        },
        {
            "key": "relationship_respectful_disagreement",
            "thought": "A disagreement can protect a relationship when both people refuse to simplify each other.",
            "moral": "Conclude with preserved complexity rather than forced agreement.",
        },
        {
            "key": "relationship_distance_as_care",
            "thought": "Sometimes care is expressed by giving another person room to act without supervision.",
            "moral": "End with chosen distance as trust, not emotional withdrawal.",
        },
    ),
    "play": (
        {
            "key": "play_safe_failure",
            "thought": "Play creates a bounded place where failure can become information instead of identity.",
            "moral": "Conclude with experimentation, not a claim that life is a game.",
        },
        {
            "key": "play_rules_create_freedom",
            "thought": "A good rule can create more surprising freedom by making expectations shared.",
            "moral": "End with the productive tension between boundary and invention.",
        },
        {
            "key": "play_serious_hobby",
            "thought": "A hobby can hold standards, memory, and community without needing to become a career.",
            "moral": "Conclude that value does not require monetization.",
        },
    ),
    "maintenance": (
        {
            "key": "maintenance_invisible_prevention",
            "thought": "Successful prevention is difficult to notice because the expected failure never arrives.",
            "moral": "End by making invisible care legible without glorifying exhaustion.",
        },
        {
            "key": "maintenance_distributed_continuity",
            "thought": "Continuity depends on small checks distributed across people and time.",
            "moral": "Conclude with continuity as a shared practice, not one person's sacrifice.",
        },
        {
            "key": "maintenance_routine_care",
            "thought": "A repeated practical routine can carry care more reliably than a dramatic gesture.",
            "moral": "End on the meaning inside the action itself, without sentimental explanation.",
        },
    ),
    "body": (
        {
            "key": "body_limit_as_information",
            "thought": "A physical limit is information about conditions, not automatically a personal failure.",
            "moral": "Conclude with adjustment and accuracy, not wellness advice.",
        },
        {
            "key": "body_sensory_knowledge",
            "thought": "Temperature, weight, balance, smell, and fatigue can reveal what an abstract plan omitted.",
            "moral": "End with embodied evidence correcting an assumption.",
        },
        {
            "key": "body_rest_recalibration",
            "thought": "Rest changes perception and judgment before it changes output.",
            "moral": "Conclude with restored proportion, not rest as a reward for productivity.",
        },
    ),
    "absurdity": (
        {
            "key": "absurdity_metric_replaces_purpose",
            "thought": "A measurement becomes absurd when people start serving it instead of the purpose it represented.",
            "moral": "End by exposing the inversion through one consequence, not a general rant.",
        },
        {
            "key": "absurdity_rule_outlives_context",
            "thought": "A rule can survive after the situation that justified it has disappeared.",
            "moral": "Conclude with the cost of unexamined continuation.",
        },
        {
            "key": "absurdity_interface_blames_user",
            "thought": "Bad arrangements often translate their own contradiction into a user's alleged mistake.",
            "moral": "End with dry clarity about responsibility, without mocking the trapped person.",
        },
    ),
    "future_practice": (
        {
            "key": "future_routine_adoption",
            "thought": "A future becomes real when an unfamiliar action turns into an ordinary routine.",
            "moral": "Conclude with changed practice rather than amazement at the tool.",
        },
        {
            "key": "future_new_role",
            "thought": "A new tool matters when it creates a new responsibility, handoff, or profession around it.",
            "moral": "End with the human role that appears, not technological destiny.",
        },
        {
            "key": "future_convenience_moves_labor",
            "thought": "Convenience rarely removes work completely; it moves work to another person or layer.",
            "moral": "Conclude by locating the displaced labor precisely.",
        },
    ),
    "creators": (
        {
            "key": "creators_editing_is_authorship",
            "thought": "Selection and removal can shape a work as strongly as adding material.",
            "moral": "End with restraint as a concrete creative decision.",
        },
        {
            "key": "creators_collaboration_friction",
            "thought": "Useful collaboration does not remove friction; it gives friction a workable form.",
            "moral": "Conclude with negotiated difference, not harmony as the goal.",
        },
        {
            "key": "creators_audience_relationship",
            "thought": "An audience is a changing relationship with expectations, not a number waiting to grow.",
            "moral": "End with responsibility to a specific audience without preaching engagement.",
        },
    ),
    "ai_models": (
        {
            "key": "ai_models_benchmark_theatre",
            "thought": "A benchmark victory matters only when the measured advantage survives contact with a real task.",
            "moral": "End by naming the practical test still owed, not by declaring intelligence solved.",
        },
        {
            "key": "ai_models_context_is_not_memory",
            "thought": "A larger context window can hold more material without necessarily understanding what deserves to persist.",
            "moral": "Distinguish storage, retrieval, and judgment through one concrete consequence.",
        },
        {
            "key": "ai_models_capability_price",
            "thought": "A model becomes culturally important when a rare capability becomes cheap enough to feel ordinary.",
            "moral": "Conclude with the behavior that changes after the price falls, not with model worship.",
        },
    ),
    "ai_agents": (
        {
            "key": "ai_agents_permission_is_product",
            "thought": "An agent's real product is not initiative but a trustworthy boundary around what it may change.",
            "moral": "End with the permission or rollback mechanism that makes autonomy usable.",
        },
        {
            "key": "ai_agents_demo_to_shift",
            "thought": "A successful agent demo lasts minutes; useful delegated work must survive interruptions, ambiguity, and tomorrow morning.",
            "moral": "Conclude with the boring reliability test the demo politely avoided.",
        },
        {
            "key": "ai_agents_handoff_tax",
            "thought": "Automation can remove clicks while creating a new job: checking what acted, why, and whether it should be undone.",
            "moral": "Locate the new handoff instead of claiming that work disappeared.",
        },
    ),
    "ai_video": (
        {
            "key": "ai_video_generation_to_direction",
            "thought": "When a plausible shot becomes cheap, taste moves from producing pixels to choosing continuity, rhythm, and what to omit.",
            "moral": "End with direction as the scarce act, not with amazement that a clip exists.",
        },
        {
            "key": "ai_video_consistency_invoice",
            "thought": "A striking synthetic clip can hide the invoice for keeping character, space, and intention consistent across a sequence.",
            "moral": "Conclude through the exact continuity failure that separates a demo from a scene.",
        },
        {
            "key": "ai_video_abundant_footage",
            "thought": "Infinite footage does not create an infinite appetite; it makes editing and attention more severe constraints.",
            "moral": "End with the cut that becomes more valuable when generation is abundant.",
        },
    ),
    "ai_audio": (
        {
            "key": "ai_audio_voice_is_identity",
            "thought": "A generated voice is not merely a sound preset when listeners attach memory, trust, and a person to it.",
            "moral": "Conclude with consent and context, not a generic fear of fakes.",
        },
        {
            "key": "ai_audio_music_selection",
            "thought": "When generating another acceptable track takes seconds, listening, choosing, and building a reason to return become the work.",
            "moral": "End with curation as authorship rather than scarcity nostalgia.",
        },
        {
            "key": "ai_audio_conversation_timing",
            "thought": "A voice model feels intelligent as much through pauses and turn-taking as through the words it produces.",
            "moral": "Conclude with timing as an interface decision, not synthetic charm.",
        },
    ),
    "ai_images": (
        {
            "key": "ai_images_prompt_to_art_direction",
            "thought": "Generating an attractive image is no longer the same as building a coherent visual system.",
            "moral": "End with the repeated decision that turns a picture into art direction.",
        },
        {
            "key": "ai_images_average_beauty",
            "thought": "A tool trained to produce plausible beauty can make taste visible by how quickly everything begins to resemble everything else.",
            "moral": "Conclude with the specific imperfection or constraint that restores identity.",
        },
        {
            "key": "ai_images_provenance",
            "thought": "The easier an image is to produce, the more valuable it becomes to know who chose it, changed it, and stands behind it.",
            "moral": "End with provenance as responsibility, not as a purity ritual.",
        },
    ),
    "ai_devices": (
        {
            "key": "ai_devices_physical_latency",
            "thought": "A mistake in a chat is a sentence; a mistake in a robot has weight, distance, and repair costs.",
            "moral": "Conclude with the physical safeguard that changes the meaning of intelligence.",
        },
        {
            "key": "ai_devices_interface_disappears",
            "thought": "When an assistant leaves the screen, its interface becomes timing, movement, attention, and the right to interrupt.",
            "moral": "End with one bodily or spatial consequence of the new interface.",
        },
        {
            "key": "ai_devices_boring_robot",
            "thought": "The useful robot may look less like science fiction and more like a machine that repeats one unpleasant task without theatre.",
            "moral": "Conclude with the ordinary routine that proves the machine matters.",
        },
    ),
    "ai_science": (
        {
            "key": "ai_science_hypothesis_speed",
            "thought": "Faster hypothesis generation increases the value of experiments that can still prove the model wrong.",
            "moral": "End with the evidence bottleneck, not the number of generated ideas.",
        },
        {
            "key": "ai_science_assistance_authority",
            "thought": "A system can assist an expert long before it deserves authority over a patient, student, or result.",
            "moral": "Conclude by drawing the boundary through one decision with consequences.",
        },
        {
            "key": "ai_science_access_tradeoff",
            "thought": "Cheap expertise can widen access while quietly standardizing which questions and explanations receive attention.",
            "moral": "End with both gains visible and one limit precisely named.",
        },
    ),
    "ai_power": (
        {
            "key": "ai_power_compute_is_policy",
            "thought": "Compute allocation looks technical until it decides who can train, experiment, compete, or inspect the result.",
            "moral": "Conclude with the actor whose options changed, not with abstract scale.",
        },
        {
            "key": "ai_power_convenience_invoice",
            "thought": "Every frictionless AI feature has an invoice in data, energy, moderation, labor, or dependency; the interesting question is who receives it.",
            "moral": "End by naming the payer without turning the post into a sermon.",
        },
        {
            "key": "ai_power_copyright_bargain",
            "thought": "The copyright argument is also a bargaining argument about whose past work becomes infrastructure for whose future product.",
            "moral": "Conclude with the concrete negotiation hidden behind moral theatre.",
        },
    ),
}


NARRATIVE_SHAPES = (
    {
        "key": "scene_tension_reversal",
        "instruction": "Open inside a concrete scene, reveal its tension, reverse the first interpretation, then conclude.",
    },
    {
        "key": "object_biography",
        "instruction": "Follow one object through use, wear, transfer, or repair until its changed meaning becomes visible.",
    },
    {
        "key": "two_people_contrast",
        "instruction": "Contrast two people's actions in the same situation without declaring either one a caricature.",
    },
    {
        "key": "process_anatomy",
        "instruction": "Trace a short process step by step and let one overlooked handoff produce the conclusion.",
    },
    {
        "key": "expectation_observation_gap",
        "instruction": "Set up a reasonable expectation, show the observed mismatch, and conclude from the mismatch.",
    },
    {
        "key": "same_detail_before_after",
        "instruction": "Return to the same physical detail before and after a small change; let the contrast carry the thought.",
    },
    {
        "key": "rule_and_exception",
        "instruction": "Show a rule through one ordinary case, then use a precise exception to expose its real purpose or flaw.",
    },
    {
        "key": "open_observation",
        "instruction": "Build from several precise observations toward a restrained, non-totalizing open conclusion.",
    },
    {
        "key": "cold_open_punch",
        "instruction": "Open with one blunt, slightly funny sentence, explain the concrete mechanism, then land a sharper second meaning.",
    },
    {
        "key": "three_signal_stack",
        "instruction": "Use three compact numbered or separated signals; make the third reframe the first two and finish with one verdict.",
    },
    {
        "key": "myth_mechanism_reality",
        "instruction": "State the attractive story, expose the actual mechanism, then show the less glamorous but more useful reality.",
    },
    {
        "key": "micro_dialogue_then_verdict",
        "instruction": "Use a two-line imagined dialogue only as a cold open, then move into precise analysis and a dry final verdict.",
    },
    {
        "key": "field_note_with_receipt",
        "instruction": "Write like a field note: observed fact, what changed, who pays or benefits, and one compact receipt-like conclusion.",
    },
    {
        "key": "before_after_without_miracle",
        "instruction": "Contrast the workflow before and after the AI shift, including the new inconvenience that the sales demo forgot.",
    },
)


SCENE_AXES = (
    {
        "key": "workbench_or_kitchen",
        "instruction": "Use a real workbench, kitchen, studio table, tool, material, or pair of hands in action.",
    },
    {
        "key": "transit_or_threshold",
        "instruction": "Use a station, crossing, entrance, corridor, elevator, route, or other threshold in use.",
    },
    {
        "key": "backstage_or_rehearsal",
        "instruction": "Use preparation before an audience arrives: rehearsal, setup, soundcheck, edit, or reset.",
    },
    {
        "key": "service_or_maintenance",
        "instruction": "Use a repair, inspection, cleaning, delivery, checklist, or quiet prevention task.",
    },
    {
        "key": "home_at_an_exact_hour",
        "instruction": "Use a domestic scene anchored to an exact hour, physical task, sound, light, or temperature.",
    },
    {
        "key": "public_queue_or_shop",
        "instruction": "Use a shop, counter, queue, waiting room, market, or public service interaction.",
    },
    {
        "key": "weather_meets_body",
        "instruction": "Use weather as a physical condition affecting movement, clothing, work, rest, or perception.",
    },
    {
        "key": "archive_or_carried_object",
        "instruction": "Use a document, recording, photograph, ticket, note, device, or inherited everyday object.",
    },
    {
        "key": "team_handoff",
        "instruction": "Use a concrete handoff between two people, shifts, roles, or stages of work.",
    },
    {
        "key": "interface_or_institutional_ritual",
        "instruction": "Use one form, button, rule, sign, script, meeting ritual, or institutional mismatch.",
    },
)


MODE_RUBRICS = {
    "signal": "SIGNAL",
    "news": "SIGNAL",
    "manual": "SIGNAL",
    "midnight": "MIDNIGHT",
    "frequency": "FREQUENCY",
    "observation": "OBSERVATION",
    "culture": "OBSERVATION",
    "future": "FUTURE FILE",
    "digest": "SIGNAL ARCHIVE",
    "archive": "SIGNAL ARCHIVE",
    "vault": "THE VAULT",
    "material": "MATERIAL / МАТЕРИЯ",
}


PLATFORM_CHANNELS = {
    "telegram": {
        "role": "intelligence, signals, automation, AI, tools, systems, short analysis",
        "format": "text-first post with optional one visual",
    },
    "vk": {
        "role": "the whole AI field through culture, products, media, people, power, and dry adult observation",
        "format": "one-cover post with deliberately varied length, pace, and structure",
    },
    "vk_public": {
        "role": "public community conversation, culture, curiosity, and dry observation",
        "format": "one concise conversational reply without markdown or a generic greeting",
    },
    "max": {
        "role": "compact signals, announcements, direct interaction",
        "format": "short post, clear point, minimal structure",
    },
}


CONTENT_PLAN = [
    {
        "mode": "signal",
        "frequency": "AI",
        "name": "AI Signal",
        "brief": (
            "A short, lively take on one concrete AI capability, product behavior, "
            "failure, or absurdity. One sharp point; no generic digital-noise thesis."
        ),
    },
    {
        "mode": "observation",
        "frequency": "ATTENTION",
        "name": "Attention Observation",
        "brief": (
            "A precise observation about a habit, profession, interface, creator, "
            "or institution changing around AI. Let the selected theme set the subject."
        ),
    },
    {
        "mode": "frequency",
        "frequency": "HUMAN",
        "name": "Frequency",
        "brief": (
            "An evening AI culture post about generated video, images, music, voice, "
            "cinema, creators, or the absurd new rituals forming around them."
        ),
    },
    {
        "mode": "future",
        "frequency": "FUTURE",
        "name": "Future File",
        "brief": (
            "A non-hype note about a concrete AI shift across models, agents, media, "
            "devices, science, work, creators, law, or power."
        ),
    },
    {
        "mode": "midnight",
        "frequency": "HUMAN",
        "name": "Midnight",
        "brief": (
            "The one deliberately slower register: a night signal grounded in a "
            "specific room, task, route, sound, memory, encounter, or physical detail."
        ),
    },
    {
        "mode": "vault",
        "frequency": "HUMAN",
        "name": "The Vault",
        "brief": (
            "A deeper thought for VOID memory. Something worth saving, not a lesson. "
            "It should help people remember what they already knew but stopped noticing."
        ),
    },
]


RUBRIC_SCHEDULE = [
    {
        "name": "Midnight",
        "voice": "void",
        "mode": "midnight",
        "frequency": "HUMAN",
        "hours": [0, 1, 2],
        "weight": 10,
        "brief": "Night-only VOID signal grounded in a specific room, task, route, sound, memory, encounter, or physical detail.",
    },
    {
        "name": "Frequency",
        "voice": "void",
        "mode": "frequency",
        "frequency": "HUMAN",
        "hours": [19, 20, 21, 22],
        "weight": 7,
        "brief": "Evening AI culture with movement: generated video, cinema, images, music, voice, creators, taste, and one concrete adult observation.",
    },
    {
        "name": "The Vault",
        "voice": "void",
        "mode": "vault",
        "frequency": "HUMAN",
        "hours": [22, 23],
        "weight": 4,
        "brief": "A deeper saved thought for the shared public. Quiet, precise, worth returning to.",
    },
    {
        "name": "Future File",
        "voice": "void",
        "mode": "future",
        "frequency": "FUTURE",
        "hours": [12, 13, 14, 15, 16, 17, 18],
        "weight": 7,
        "brief": "A concrete non-hype AI shift across models, agents, video, audio, images, robots, science, work, law, or power.",
    },
    {
        "name": "Observation",
        "voice": "void",
        "mode": "observation",
        "frequency": "ATTENTION",
        "hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
        "weight": 5,
        "brief": "A precise, lively observation about a habit, profession, interface, creator, or institution changing around AI.",
    },
    {
        "name": "Signal",
        "voice": "void",
        "mode": "signal",
        "frequency": "AI",
        "hours": [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
        "weight": 6,
        "brief": "An original VOID take on one concrete AI capability, product behavior, failure, or absurdity.",
    },
    {
        "name": "News Signal",
        "voice": "news",
        "mode": "news",
        "frequency": "AI",
        "hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
        "weight": 8,
        "brief": "Fresh verified signal from the whole AI segment, only when the source has a concrete fact worth catching.",
    },
]


# Telegram and VK intentionally share one editorial rubric matrix. Platform
# differences belong to the platform context and publisher, not to a second
# copy of the subject/tone schedule that can silently drift back to sameness.
# Copy every row (and its mutable hours list) so tests or administrative code
# cannot mutate one platform's runtime schedule through the other.
TELEGRAM_VOID_SCHEDULE = [
    {**row, "hours": list(row["hours"])} for row in RUBRIC_SCHEDULE
]




def platform_context(platform: str = "telegram") -> str:
    channel = PLATFORM_CHANNELS.get(platform, PLATFORM_CHANNELS["telegram"])
    return (
        f"PLATFORM: {platform}\n"
        f"ROLE: {channel['role']}\n"
        f"FORMAT: {channel['format']}"
    )
