import { Serializer } from 'survey-core';

// Make ranking reorder respond to a normal drag on touch devices instead of
// requiring a 500ms long-press first. With the default longTap=true, iOS
// Safari treats the initial touch as a scroll gesture and the page scrolls
// instead of starting the drag (see GH #49). SurveyJS's own docs recommend
// disabling longTap when users naturally swipe-to-drag rather than long-press.
//
// Idempotent: re-importing this module does not re-toggle the default.
// Explicit null-check so a future survey-core upgrade that renames the
// property fails loudly here rather than with a confusing "Cannot set property
// 'defaultValue' of null" at the assignment site.
const longTap = Serializer.findProperty('ranking', 'longTap');
if (!longTap) {
  throw new Error(
    "survey-core ranking property 'longTap' not found — has the API changed? See GH #49.",
  );
}
longTap.defaultValue = false;
