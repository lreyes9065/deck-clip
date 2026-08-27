export type Clip = {
  id: string;
  app_id: string;
  game_name: string;
  recorded_at: string;
  duration_seconds: number | null;
  session_count: number;
};

export type ExportItem = { id: string; name?: string };
export type JobClip = { id: string; display_name: string; progress: number; state: string; output?: string; error?: string };
export type Job = { state: string; progress: number; output_dir: string; clips: JobClip[]; error?: string };
export type GameGroup = { id: string; name: string; clips: Clip[] };
export type ExportedFile = { filename: string; size_bytes: number; modified_at: string };
export type TransferStatus = {
  state: string;
  filename?: string;
  url?: string;
  expires_at?: string;
  downloads?: number;
  bytes_sent?: number;
  qr?: string[];
};
