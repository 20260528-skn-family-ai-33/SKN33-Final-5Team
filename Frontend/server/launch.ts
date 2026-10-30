// Agent creation is the default demo. Existing workflow engine stays reusable.
process.env.A4B_FOCUSED_DEMO ??= '1';
process.env.PORT ??= '3030';
// Product entry uses the server Qwen connection; legacy OpenAI accounts must not be called.
process.env.A4B_REQUIRE_LOCAL_MODEL ??= '1';
await import('./index');
export {};
