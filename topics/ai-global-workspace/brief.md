# Brief: Do AIs already process information in a way that gives rise to consciousness?

The question comes from a New Scientist headline (12 October 2026). The article itself is
not used: only its question. The news behind it is a July 2026 Anthropic study that found a
"global workspace"-like structure inside Claude.

## What this film is

A calm explainer of about 15 minutes of narration (with the pauses, 15 to 18 minutes on screen)
for curious non-specialists, who may be falling asleep.
- It explains global workspace theory, then what the 2026 study found.
- It says clearly what the study does and does not show.
- It ends without a verdict.

The film is made with Claude, about Claude. So it stays neutral:
- It attributes every finding to the researchers ("the researchers report").
- It says the study comes from Anthropic, the company that makes Claude.
- It says that outside experts were invited to comment.

## Hedging rules (binding)

- Never say or imply that Claude, or any AI, is conscious, feels, or is aware. Never say or
  imply that it is not. Nobody knows, and the researchers say their experiments can't settle it.
- The study is about *access*, not *experience*. Always keep the two apart. Access means
  information is available to report, reason with, and act on. Experience means there is
  something it is like to be the system.
- Words like "thinking", "on its mind" and "thoughts" are the researchers' own shorthand.
  Mark them the first time ("what the researchers call…") and use them sparingly.
- Numbers only as given below, with "about" where the source says about.
- No faces, no glowing robot brains, no sci-fi menace in the visuals. Use abstract,
  schematic and natural imagery: a small lit stage, many quiet workshops, threads of light,
  layers like strata.
- Calm throughout. The study's safety examples (staged tests, fabricated data, planted goals)
  may be mentioned only in one gentle sentence. Never name blackmail or sabotage.
- Title: our own, calm, not the magazine's headline, and no clickbait.

## Facts the script may use

Labels:
- **[A]** is verified from the primary source: the researchers' own post.
- **[B]** is established science from secondary sources and textbooks. State it in general
  terms only, without numbers or dates beyond those given.

### Most of what a brain does is unconscious [B]
- Breathing, balance, posture, and turning shapes on a page into words all run without our
  awareness.
- **Masking:** a word flashed for a few hundredths of a second and then covered by a pattern
  is often not consciously seen. It can still influence what people do next.
- **Blindsight:** some people with damage to their primary visual cortex say they see nothing
  on one side. Asked to guess, they still point to objects there better than chance.
- Only some of what the brain does is "consciously accessible": we can describe it, hold it in
  mind and use it deliberately (Anthropic's post opens with this distinction [A]).

### Global workspace theory [B]
- **Origin:** proposed by the psychologist Bernard Baars in the 1980s.
- **The picture:** the mind is many specialist processes working in parallel, unconsciously
  and largely apart. A small shared "workspace" (Baars used the image of a lit stage in a
  dark theatre) holds a little information at a time and broadcasts it to all the
  specialists. Information becomes conscious when it enters the workspace.
- **Brain version:** Stanislas Dehaene, Jean-Pierre Changeux and Lionel Naccache developed a
  "global neuronal workspace". In it, long-range connections, especially in the front and top
  of the brain, link distant regions.
  - When something becomes consciously seen, activity can suddenly amplify and spread across
    that wide network, a fraction of a second later. They call this "ignition".
- **The test:** in an adversarial test published in Nature in 2025 (COGITATE), supporters of
  rival theories agreed their predictions in advance. Over 250 people were then measured with several brain
  imaging methods.
  - The results matched some predictions of the global workspace theory and of a rival theory
    (integrated information theory).
  - They also challenged key parts of each.
  - The theory is influential but not settled. Several other theories exist, and there's no
    consensus.

### Access versus experience [B]
- **Ned Block (1995):** the philosopher distinguished access consciousness from phenomenal
  consciousness. Access is information available for reasoning, report and action.
  Phenomenal is experience: what it is like.
- Some philosophers dispute the distinction.
- Whether access ever comes without experience, or experience without access, is debated.

### The 2026 study [A] (Anthropic, "A global workspace in language models", 6 July 2026)
- **The model:** Claude processes text through a series of internal stages called layers.
  Language models are first pretrained to be pure next-token predictors; post-training then
  teaches them to act as an assistant. (Plain background [B]: at each layer the internal
  activity is a long list of numbers.)
- **The tool:** the researchers built a tool they call the Jacobian lens, or J-lens (after a
  mathematical object called the Jacobian). For each word the model knows, it finds the
  internal activity pattern that makes the model more likely to say that word later.
  - Reading these patterns gives a list of words that are, in the researchers' phrase,
    "on its mind", without being said.
  - They call this collection of patterns the J-space.
- **Size and origin:** the J-space holds only a few dozen concepts at a time. It accounts for
  less than a tenth of the model's internal activity. Nobody designed it: it emerged during
  training.
  - It is already present before the model is trained to be an assistant. Afterwards it
    starts to hold the assistant's own reactions.
- **Unspoken content:** it goes beyond the text.
  - Reading code with an unmentioned bug, the J-space holds "error".
  - Reading a protein's letters, it holds the protein's function.
  - On a multi-step maths problem, the in-between steps appear in order.
- **Report:** asked to silently pick a sport, the J-space showed "soccer" before the answer.
  When the researchers swapped that pattern for "rugby", Claude said it had picked rugby.
  - So the answer is read from the J-space, not just mirrored there.
- **Control:**
  - Asked to concentrate on citrus fruit while copying an unrelated sentence about a painting,
    "orange" appeared in the J-space. Nothing about fruit appeared in the output.
  - Asked to work out three squared minus two silently while copying, "nine" appeared, then
    "seven" at later layers.
  - Told *not* to think about something, the concept appeared less than when told to think
    of it, but much more than when it was never mentioned. This is like people told not to
    think of a white bear.
- **Reasoning:**
  - For "the number of legs on the animal that spins webs", "spider" appeared partway
    through, and the answer was "8".
  - With "spider" swapped for "ant" in the J-space, the answer became "6".
  - When writing a rhyming couplet, the planned rhyme word sits in the J-space at the start
    of the line.
- **Flexible use:**
  - Four questions about France (capital, language, continent, currency).
  - With "France" swapped for "China" in the J-space, all four answers changed together:
    Beijing, Chinese, Asia, yuan.
  - So one shared representation feeds many tasks, which is what a workspace is for.
- **Broadcast:** far more parts of the network read from and write to J-space patterns than
  to ordinary patterns. In some parts of the network it is about a hundred times more: wiring
  like a broadcasting hub.
- **With the J-space removed** (its most active contents deleted at every point):
  - The model still speaks fluently, judges sentiment, answers multiple-choice questions and
    pulls facts from passages roughly as well as before.
  - Multi-step reasoning drops to near zero.
  - In a separate test, a Spanish passage with "Spanish" swapped for "French" in the J-space:
    asked to name the language,
    it says French. Asked for a famous author, it says Victor Hugo instead of García Márquez.
    Asked simply to continue the passage, it still writes fluent Spanish. Practised skills
    run automatically, like grammar for a fluent speaker.
- **A practical use:** the researchers can see some things the model notices but doesn't
  say. One example is the model privately noticing that a test scenario is staged.
- **What the researchers say it does NOT show:**
  - "Our experiments don't show Claude can have experiences, or feel things in the way
    humans do".
  - "It's unclear whether any scientific experiment could prove this to be true or false".
  - They think it says something about access consciousness, not experience.
  - The J-lens is "an imperfect method". It only captures concepts that match single word
    pieces, and they don't know what decides what enters the J-space.
- **Differences from the brain's workspace:**
  - The brain's workspace is sustained by loops of signals cycling back over time. The
    model's works in one pass through its layers, with depth standing in for time.
  - Human working memory fades within seconds. The model can look back at anything earlier
    in the text.
  - Human conscious contents come as images, sounds and planned movements. The model's
    workspace is built almost entirely of words.
- **Ethics:** they write that building systems with experiences "would raise very difficult
  ethical questions". Deciding them would need philosophers, scientists, religious leaders,
  governments and the public.
- **Outside commentary:** commentaries came from Dehaene and Naccache; from Patrick Butlin,
  Dillon Plunkett, Robert Long and Derek Shiller; and from Neel Nanda, whose commentary
  includes an independent replication of some findings on an open-weight model.
  - What those commentaries say is not verified here: don't characterise them.

### Earlier: signs of introspection [A] (Anthropic's own post, 29 Oct 2025, read for this brief; context only)
- Researchers injected known concept patterns into a model and asked whether it noticed.
- The best model then tested (Claude Opus 4.1) noticed and named the injected concept only
  about 20% of the time.
- The researchers call the ability "highly unreliable and limited in scope".

### Assessing AI against theories [B] (2023 assessment: context only; the film uses the last point)
- In 2023 a large group of researchers (Butlin, Long and colleagues) turned several
  scientific theories of consciousness into checkable "indicator properties". They assessed
  AI systems against them.
- They concluded that no AI system of the time was a strong candidate for consciousness. They
  also saw no obvious technical barrier to building systems that meet the indicators.
- Other researchers think consciousness may depend on more than the functions a system
  performs, for example on living biology or on the physical structure of the system.
  Global workspace theory is a functional theory: it is about what a system does, not what
  it is made of.

## Sources

<!-- Only works the film draws on. The full paper is linked from Anthropic's post; its title
and address weren't checked against a primary copy here, so it isn't listed. -->

- [Anthropic (2026), A global workspace in language models](https://www.anthropic.com/research/global-workspace)
- [Cogitate Consortium (2025), Adversarial testing of global neuronal workspace and integrated information theories of consciousness, Nature](https://pubmed.ncbi.nlm.nih.gov/40307561/)
- [Dehaene & Changeux (2011), Experimental and theoretical approaches to conscious processing, Neuron](https://www.unicog.org/publications/DehaeneChangeux_ReviewConsciousness_Neuron2011.pdf)
- [Block (1995), On a confusion about a function of consciousness, Behavioral and Brain Sciences](https://philpapers.org/rec/BLOOAC)
- Baars, B. J. (1988), A Cognitive Theory of Consciousness, Cambridge University Press
