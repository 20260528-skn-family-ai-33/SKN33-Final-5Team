/** Preserve source order and individual errors without flooding the local server. */
export async function settleWithLimit<T, R>(items: readonly T[], limit: number, task: (item: T, index: number) => Promise<R>, shouldContinue = () => true): Promise<PromiseSettledResult<R>[]> {
    if (!Number.isInteger(limit) || limit < 1)
        throw new RangeError('동시 요청 수는 1 이상의 정수여야 합니다.');
    const results: PromiseSettledResult<R>[] = new Array(items.length);
    let cursor = 0;
    async function worker() { while (cursor < items.length) {
        const index = cursor++;
        if (!shouldContinue()) {
            results[index] = { status: 'rejected', reason: new Error('취소된 작업입니다.') };
            continue;
        }
        try {
            results[index] = { status: 'fulfilled', value: await task(items[index], index) };
        }
        catch (reason) {
            results[index] = { status: 'rejected', reason };
        }
    } }
    await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
    return results;
}
