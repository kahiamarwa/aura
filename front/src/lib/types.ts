export interface Attachment {
  file_path: string;
  file_name: string;
  type: string;
  pdf_file_path?: string;
  pdf_file_name?: string;
}

export interface ConversationEntry {
  id: string;
  timestamp: Date;
  command: string;
  response: string;
  attachments?: Attachment[];
  isStreaming?: boolean;
  toolInProgress?: string;
}
