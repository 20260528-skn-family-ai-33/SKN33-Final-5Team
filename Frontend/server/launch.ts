// Agent creation is the default demo. Existing workflow engine stays reusable.
process.env.A4B_FOCUSED_DEMO ??= '1';
process.env.PORT ??= '3030';
await import('./index');
export {};
