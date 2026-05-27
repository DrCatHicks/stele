// Curated starter skeletons for the authoring editor — minimal, known-good
// definitions an author can begin from, covering the publishable surface
// (radiogroup/dropdown/text/comment + visibleIf branching). These are *starters*,
// distinct from the annotated reference library in patterns/ (which is the
// canonical, gate-validated set and LLM-authoring context). starterTemplates.test
// guarantees each stays valid JSON with the expected structure.

export interface StarterTemplate {
  id: string;
  label: string;
  description: string;
  definition: Record<string, unknown>;
}

export const STARTER_TEMPLATES: StarterTemplate[] = [
  {
    id: 'blank',
    label: 'Blank survey',
    description: 'One empty page — add your own questions.',
    definition: { title: 'Untitled survey', pages: [{ name: 'page1', elements: [] }] },
  },
  {
    id: 'single_select',
    label: 'Single-select question',
    description: 'A radiogroup with a few choices.',
    definition: {
      title: 'Single-select starter',
      pages: [
        {
          name: 'page1',
          elements: [
            {
              type: 'radiogroup',
              name: 'satisfaction',
              title: 'How satisfied are you?',
              choices: ['Very satisfied', 'Satisfied', 'Neutral', 'Dissatisfied'],
            },
          ],
        },
      ],
    },
  },
  {
    id: 'free_text',
    label: 'Free-text question',
    description: 'An open comment field (defaults to high PII risk).',
    definition: {
      title: 'Free-text starter',
      pages: [
        {
          name: 'page1',
          elements: [
            {
              type: 'comment',
              name: 'feedback',
              title: 'Anything else you would like to share?',
            },
          ],
        },
      ],
    },
  },
  {
    id: 'branching',
    label: 'Branching (conditional question)',
    description: 'A follow-up shown only for a specific answer (visibleIf).',
    definition: {
      title: 'Branching starter',
      pages: [
        {
          name: 'page1',
          elements: [
            {
              type: 'radiogroup',
              name: 'has_pet',
              title: 'Do you have a pet?',
              choices: ['Yes', 'No'],
            },
            {
              type: 'text',
              name: 'pet_name',
              title: "What is your pet's name?",
              visibleIf: "{has_pet} = 'Yes'",
            },
          ],
        },
      ],
    },
  },
];
