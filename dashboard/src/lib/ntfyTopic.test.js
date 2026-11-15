import { test, expect } from 'vitest';
import { generateNtfyTopic } from './ntfyTopic';

test('generated topics are long, random and match the backend rule', () => {
  const a = generateNtfyTopic();
  const b = generateNtfyTopic();
  expect(a).toMatch(/^clippyme-[a-z0-9]{16}$/);
  expect(a).toMatch(/^[A-Za-z0-9_-]{8,64}$/); // notifier.TOPIC_RE
  expect(a).not.toBe(b);
});
