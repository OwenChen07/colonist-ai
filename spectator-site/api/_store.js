import { createClient } from 'redis';

// One hash: field = bot VM, value = its latest status JSON.
export const KEY = 'spectator:bots';

let client;
export async function redis() {
  if (!client) {
    client = createClient({ url: process.env.REDIS_URL, socket: { connectTimeout: 5000 } });
    client.on('error', () => {});
    await client.connect();
  }
  return client;
}
