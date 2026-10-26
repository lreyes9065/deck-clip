export type Clip = {
  id: string;
  app_id: string;
  game_name: string;
  recorded_at: string;
  duration_seconds: number | null;
  session_count: number;
  /** Steam wrote to this clip in the last few seconds; it may still be saving. */
  saving?: boolean;
};

export type ThumbnailResult = { status: "busy" } | { status: "ready"; image: string | null };
export type ActiveExport = { job_id: string; job: Job };

export type ExportItem = { id: string; name?: string };
export type JobClip = { id: string; display_name: string; progress: number; state: string; stage?: string; output?: string; error?: string };
export type RecentStatus = Job & { id: string; started_at: string; finished_at?: string; details?: string; free_bytes?: number };
export type Job = { state: string; progress: number; output_dir: string; clips: JobClip[]; error?: string };
export type GameGroup = { id: string; name: string; clips: Clip[] };
export type ExportedFile = { filename: string; size_bytes: number; modified_at: string };
export type TransferStatus = {
  state: string;
  filename?: string;
  filenames?: string[];
  file_count?: number;
  completed_files?: number;
  url?: string;
  expires_at?: string;
  downloads?: number;
  bytes_sent?: number;
  qr?: string[];
};
