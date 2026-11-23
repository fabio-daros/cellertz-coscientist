/** What a round says about where it started and why it differs, shared by both views. */

// The route refuses anything longer, so the box stops here rather than letting
// the person write a lesson the server will not record.
export const LESSON_MAX = 500;

export type RoundSeed = {
  design?: string | null;
  attempt_id?: string | null;
  parent_round_id?: string | null;
};

export type RoundLesson = {
  status?: string;
  declared?: string | null;
  learned_from?: {
    status?: string | null;
    design?: string | null;
    scoring_protocol?: string | null;
    ranked_designs?: number | null;
  } | null;
};

type HasSeedAndLesson = { seeded_from?: RoundSeed | null; lesson?: RoundLesson | null };

export function seedSentence(card: HasSeedAndLesson): string {
  const seed = card.seeded_from;
  if (!seed || !seed.design) {
    return "The bare scaffold. The parent accepted no design to start from.";
  }
  return `Parent design ${seed.design}${seed.attempt_id ? ` (${seed.attempt_id})` : ""}.`;
}

export function lessonSentence(card: HasSeedAndLesson): string {
  const lesson = card.lesson;
  if (!lesson || lesson.status !== "declared" || !lesson.declared) {
    return "None declared.";
  }
  const seen = lesson.learned_from;
  const measured = seen?.status
    ? ` Parent: ${seen.status}${seen.design ? `, design ${seen.design}` : ""}${
        seen.scoring_protocol ? `, scored under ${seen.scoring_protocol}` : ""
      }${typeof seen.ranked_designs === "number" ? `, ${seen.ranked_designs} ranked` : ""}.`
    : "";
  return `${lesson.declared}${measured}`;
}
