export type HudMode = 'ctrlf' | 'pins' | 'ctrlz' | 'crew' | 'scan';

export const VOICE_HINTS: Record<HudMode, {phrase: string; description: string}> = {
  ctrlf: {
    phrase: 'Where is the water bottle?',
    description: 'Tap Ask & speak: "Where is the water bottle?"',
  },
  pins: {
    phrase: 'Hey Meta, pin a warning here',
    description: 'Voice: "Hey Meta, pin a warning here"',
  },
  ctrlz: {
    phrase: 'Hey Meta, undo room changes',
    description: 'Voice: "Hey Meta, undo room changes"',
  },
  crew: {
    phrase: 'Hey Meta, what is our WorldGit status?',
    description: 'Voice: "Hey Meta, what is our WorldGit status?"',
  },
  scan: {
    phrase: 'Hey Meta, start IMU scan',
    description: 'Voice: "Hey Meta, start IMU scan"',
  },
};

interface ModelContextTool {
  name: string;
  description: string;
  inputSchema: {
    type: 'object';
    properties: Record<string, {type: string; description: string}>;
    required?: string[];
  };
  annotations?: {readOnlyHint?: boolean};
  execute: (args: Record<string, unknown>, extra?: {signal?: AbortSignal}) => Promise<string> | string;
}

interface ModelContext {
  registerTool?: (tool: ModelContextTool, options?: {signal?: AbortSignal}) => unknown;
}

declare global {
  interface Document {
    modelContext?: ModelContext;
  }
}

export interface WebMcpHandlers {
  getStateSummary: () => Record<string, unknown>;
  findObject: (query: string) => Promise<Record<string, unknown>>;
  pinMemory: (text: string, kind: string) => Promise<Record<string, unknown>>;
  runCtrlZ: () => Promise<Record<string, unknown>>;
  startImuScan: () => Promise<Record<string, unknown>>;
  stopImuScan: () => Promise<Record<string, unknown>>;
}

function safeRegister(ctx: ModelContext, tool: ModelContextTool, signal?: AbortSignal) {
  if (!ctx.registerTool) return;
  try {
    const result = ctx.registerTool(tool, signal ? {signal} : undefined);
    if (typeof (result as PromiseLike<unknown>)?.then === 'function') {
      (result as PromiseLike<unknown>).then(undefined, (err: unknown) => {
        console.error(`${tool.name} registration failed`, err);
      });
    }
  } catch (err) {
    console.error(`${tool.name} registration failed`, err);
  }
}

export function registerWorldGitTools(handlers: WebMcpHandlers, signal?: AbortSignal) {
  const ctx = document.modelContext;
  if (!ctx?.registerTool) return;

  safeRegister(
    ctx,
    {
      name: 'worldgit_get_state',
      description:
        'Read the current WorldGit HUD mode, live compass/IMU telemetry, active Ctrl+F target, nearby spatial pins, and diff count.',
      inputSchema: {
        type: 'object',
        properties: {},
      },
      annotations: {readOnlyHint: true},
      execute: () => JSON.stringify(handlers.getStateSummary()),
    },
    signal,
  );

  safeRegister(
    ctx,
    {
      name: 'worldgit_find_object',
      description:
        'Ctrl+F search across multiplayer glasses memory for a physical object and return walking directions.',
      inputSchema: {
        type: 'object',
        properties: {
          query: {
            type: 'string',
            description: 'Object name to find, e.g. "red drill", "car keys", "socket wrench".',
          },
        },
        required: ['query'],
      },
      execute: async ({query}) => {
        const q = String(query ?? '').trim();
        if (!q) {
          return JSON.stringify({
            error: true,
            message: 'No object name was provided.',
            next_action: 'Ask the wearer which object they want to find.',
          });
        }
        const res = await handlers.findObject(q);
        return JSON.stringify({
          ...res,
          next_action: 'Read the walking directions in one short sentence. Stop and talk.',
        });
      },
    },
    signal,
  );

  safeRegister(
    ctx,
    {
      name: 'worldgit_pin_memory',
      description:
        'Drop a spatial voice note, safety warning, or Friend World memory at the wearer current location.',
      inputSchema: {
        type: 'object',
        properties: {
          text: {
            type: 'string',
            description: 'Message or memory text to anchor here.',
          },
          kind: {
            type: 'string',
            description: 'One of: warning, note, or friend.',
          },
        },
        required: ['text'],
      },
      execute: async ({text, kind}) => {
        const msg = String(text ?? '').trim();
        if (!msg) {
          return JSON.stringify({
            error: true,
            message: 'Pin text cannot be empty.',
            next_action: 'Ask the wearer what note they want to pin here.',
          });
        }
        const res = await handlers.pinMemory(msg, String(kind ?? 'note'));
        return JSON.stringify({
          ...res,
          next_action: 'Confirm the spatial pin was saved in one short sentence. Stop and talk.',
        });
      },
    },
    signal,
  );

  safeRegister(
    ctx,
    {
      name: 'worldgit_undo_diff',
      description:
        'Compute Reality git diff and show step-by-step Ctrl+Z instructions to restore the room.',
      inputSchema: {
        type: 'object',
        properties: {},
      },
      execute: async () => {
        const res = await handlers.runCtrlZ();
        return JSON.stringify({
          ...res,
          next_action: 'Summarize the first two Ctrl+Z restore steps concisely. Stop and talk.',
        });
      },
    },
    signal,
  );

  safeRegister(
    ctx,
    {
      name: 'worldgit_start_imu_scan',
      description:
        'Enable 9-axis glasses IMU (compass, gyro, accelerometer) tracking and start recording a motion log for video sync.',
      inputSchema: {
        type: 'object',
        properties: {},
      },
      execute: async () => {
        const res = await handlers.startImuScan();
        return JSON.stringify({
          ...res,
          next_action: 'Confirm 9-axis IMU recording started and remind wearer to film now. Stop and talk.',
        });
      },
    },
    signal,
  );

  safeRegister(
    ctx,
    {
      name: 'worldgit_stop_imu_scan',
      description:
        'Stop 9-axis IMU recording and upload the timestamped session to the WorldGit Hub.',
      inputSchema: {
        type: 'object',
        properties: {},
      },
      execute: async () => {
        const res = await handlers.stopImuScan();
        return JSON.stringify({
          ...res,
          next_action: 'Confirm IMU log uploaded for auto-matching with the uploaded clip. Stop and talk.',
        });
      },
    },
    signal,
  );
}
