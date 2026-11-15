// ntfy topics are public names: anyone who guesses one reads it. Long random.
export function generateNtfyTopic() {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789';
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return 'clippyme-' + Array.from(bytes, (b) => alphabet[b % alphabet.length]).join('');
}
