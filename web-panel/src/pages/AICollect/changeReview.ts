import { applyPatch, createTwoFilesPatch, reversePatch, structuredPatch, type StructuredPatch } from 'diff';
import { parseDiffFromFile } from '@pierre/diffs';
import type { SavedTurn } from './chatStore';

export const reviewFiles = [{ key: 'template', path: 'template.yaml', step: 2 }, { key: 'adapter', path: 'adapter.py', step: 3 }] as const;
export type ReviewFileKey = typeof reviewFiles[number]['key'];
export type ReviewFiles = Record<ReviewFileKey, string>;
export type ReviewRevision = ReviewFiles & { id: string; createdAt: string; message: string; turnId: number };
export type ReviewComment = { id: string; revisionId: string; path: ReviewFileKey; side: 'additions' | 'deletions'; start: number; end: number; body: string; resolved: boolean };
export type ReviewState = { revisions: ReviewRevision[]; staged: Partial<ReviewFiles>; comments: ReviewComment[] };
export const emptyFiles: ReviewFiles = { template: '', adapter: '' };
export const emptyReview = (): ReviewState => ({ revisions: [], staged: {}, comments: [] });

export function reviewDiff(path: string, before: string, after: string, hideWhitespace = false) {
  const normalize = (text: string) => hideWhitespace ? text.replace(/[^\S\n]/g, '') : text;
  const diff = parseDiffFromFile(before ? { name: path, contents: normalize(before) } : null, { name: path, contents: normalize(after) }, { context: 3 }, true);
  if (hideWhitespace) {
    // Compare without white space, but render the original text and line coordinates.
    diff.deletionLines = before.match(/[^\n]*\n|[^\n]+$/g) ?? [];
    diff.additionLines = after.match(/[^\n]*\n|[^\n]+$/g) ?? [];
    diff.deletionCharacterCount = before.length;
    diff.additionCharacterCount = after.length;
  }
  return diff;
}

export function reviewHistory(turns: SavedTurn[]): ReviewRevision[] {
  const result: ReviewRevision[] = [];
  for (const turn of turns) {
    const previous = result[result.length - 1] ?? emptyFiles;
    const files = { template: turn.template || previous.template, adapter: turn.adapter || previous.adapter };
    if (turn.review?.revisions.length) {
      result.push(...turn.review.revisions);
      const last = result[result.length - 1];
      if (last.template !== turn.template || last.adapter !== turn.adapter) {
        result.push({ template: turn.template, adapter: turn.adapter, id: `response-${turn.id}-current`, turnId: turn.id, createdAt: turn.createdAt ?? '', message: turn.prompt });
      }
    } else if (turn.template || turn.adapter) {
      result.push({ ...files, id: `response-${turn.id}`, turnId: turn.id, createdAt: turn.createdAt ?? '', message: turn.prompt });
    }
  }
  return result;
}

export function recordCorrection(turn: SavedTurn, current: ReviewFiles, files: ReviewFiles, message: string): SavedTurn {
  if (files.template === current.template && files.adapter === current.adapter) return turn;
  const review = turn.review ?? emptyReview();
  const revisions = [...review.revisions];
  const last = revisions[revisions.length - 1];
  if (!last || last.template !== current.template || last.adapter !== current.adapter) revisions.push({ template: current.template, adapter: current.adapter, id: last ? crypto.randomUUID() : `response-${turn.id}`, turnId: turn.id, createdAt: turn.createdAt ?? '', message: turn.prompt });
  return { ...turn, template: files.template, adapter: files.adapter, review: { ...review, revisions: [...revisions, { template: files.template, adapter: files.adapter, id: crypto.randomUUID(), turnId: turn.id, createdAt: new Date().toISOString(), message }] } };
}

export function filePatch(path: string, before: string, after: string) {
  return createTwoFilesPatch(before ? `a/${path}` : '/dev/null', after ? `b/${path}` : '/dev/null', before, after);
}

export function changeHunk(path: string, before: string, after: string, hunkIndex: number, target: string, reverse = false) {
  const patch = structuredPatch(`a/${path}`, `b/${path}`, before, after, '', '', { context: 3 });
  const hunk = patch.hunks[hunkIndex];
  if (!hunk) throw new Error('This change has moved. Refresh the comparison before trying again.');
  const selected: StructuredPatch = { ...patch, hunks: [hunk] };
  const next = applyPatch(target, reverse ? reversePatch(selected) : selected);
  if (next === false) throw new Error('This change no longer applies to the current file. Refresh before trying again.');
  return next;
}

// Reconstruct keys explicitly: PostgreSQL JSONB does not preserve object key order.
export function canonicalReview(review?: ReviewState): ReviewState | undefined {
  if (!review) return undefined;
  return {
    revisions: review.revisions.map(({ id, createdAt, message, turnId, template, adapter }) => ({ id, createdAt, message, turnId, template, adapter })),
    staged: { ...(review.staged.template !== undefined ? { template: review.staged.template } : {}), ...(review.staged.adapter !== undefined ? { adapter: review.staged.adapter } : {}) },
    comments: review.comments.map(({ id, revisionId, path, side, start, end, body, resolved }) => ({ id, revisionId, path, side, start, end, body, resolved })),
  };
}
