import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { createSaveQueue } from "./saveQueue.ts";

/** Let pending promise callbacks run. */
async function settle() {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

function deferred() {
  let resolve!: () => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

test("a burst of changes is saved once, with the last value", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const saved: number[] = [];
    const q = createSaveQueue<number>(async (v) => { saved.push(v); }, 800);
    q.schedule(1);
    mock.timers.tick(300);
    q.schedule(2);
    mock.timers.tick(300);
    q.schedule(3);
    assert.equal(q.state(), "pending");
    assert.ok(q.unsaved());
    mock.timers.tick(799);
    await settle();
    assert.deepEqual(saved, []);
    mock.timers.tick(1);
    await settle();
    assert.deepEqual(saved, [3]);
    assert.equal(q.state(), "saved");
    assert.ok(!q.unsaved());
  } finally {
    mock.timers.reset();
  }
});

test("a change made during a save is saved right after it, never in parallel", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const order: string[] = [];
    let running = 0;
    const gates = [deferred(), deferred()];
    let call = 0;
    const q = createSaveQueue<string>(async (v) => {
      running += 1;
      assert.equal(running, 1, "two saves overlapped");
      order.push(`start ${v}`);
      await gates[call++].promise;
      order.push(`end ${v}`);
      running -= 1;
    }, 800);
    q.schedule("a");
    mock.timers.tick(800);
    await settle();
    assert.equal(q.state(), "saving");
    q.schedule("b"); // while "a" is in flight
    mock.timers.tick(800); // b's timer fires during the flight
    await settle();
    gates[0].resolve();
    await settle();
    gates[1].resolve();
    await q.flush();
    assert.deepEqual(order, ["start a", "end a", "start b", "end b"]);
    assert.equal(q.state(), "saved");
  } finally {
    mock.timers.reset();
  }
});

test("a failed save keeps the latest value for retry, and flush reports it", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const saved: number[] = [];
    let fail = true;
    const q = createSaveQueue<number>(async (v) => {
      if (fail) throw new Error("offline");
      saved.push(v);
    }, 800);
    q.schedule(7);
    assert.equal(await q.flush(), false);
    assert.equal(q.state(), "error");
    assert.ok(q.unsaved());
    fail = false;
    assert.equal(await q.retry(), true);
    assert.deepEqual(saved, [7]);
    assert.equal(q.state(), "saved");
  } finally {
    mock.timers.reset();
  }
});

test("flush saves a pending change immediately, without waiting for the timer", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const saved: number[] = [];
    const q = createSaveQueue<number>(async (v) => { saved.push(v); }, 800);
    q.schedule(5);
    assert.equal(await q.flush(), true);
    assert.deepEqual(saved, [5]);
    mock.timers.tick(800);
    await settle();
    assert.deepEqual(saved, [5]); // the cancelled timer doesn't save twice
  } finally {
    mock.timers.reset();
  }
});
