import client, { unwrap } from '@/services/api';

export type SavedAttachment = { name: string; type: string; content: string };
export type SavedTurn = { id: number; prompt: string; reply: string; template: string; adapter: string; step: number; done: number; complete: boolean; waiting?: 'template' | 'adapter'; vote?: 'up' | 'down'; attachments?: SavedAttachment[] };
export type SavedChat = { id: string; title: string; turns: SavedTurn[]; pinned: boolean; archived: boolean; revision: number };
export type ChatSummary = Omit<SavedChat, 'turns'>;
export type ShareSnapshot = { title: string; turns: SavedTurn[]; created_at?: string };

export const newChat = (): SavedChat => ({ id: crypto.randomUUID(), title: 'New chat', turns: [], pinned: false, archived: false, revision: 0 });
export const chatUrl = (id: string) => `${window.location.origin}/scout?chat=${encodeURIComponent(id)}`;
const fileContents = new WeakMap<File, Promise<SavedAttachment>>();
export async function persistTurns(turns: (SavedTurn & { files?: File[] })[]): Promise<SavedTurn[]> {
  return Promise.all(turns.map(async ({ files, ...turn }) => ({ ...turn, attachments: files?.length ? await Promise.all(files.map(file => {
    if (!fileContents.has(file)) fileContents.set(file, new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve({ name: file.name, type: file.type, content: String(reader.result).split(',')[1] ?? '' });
      reader.onerror = () => reject(new Error(`Unable to save attachment ${file.name}`));
      reader.readAsDataURL(file);
    }));
    return fileContents.get(file)!;
  })) : turn.attachments ?? [] })));
}
export function restoreFiles(turn: SavedTurn): File[] {
  return (turn.attachments ?? []).map(file => new File([Uint8Array.from(atob(file.content), char => char.charCodeAt(0))], file.name, { type: file.type }));
}
export const markdown = (chat: ShareSnapshot) => `# ${chat.title}\n\n${chat.turns.map(turn => `## You\n\n${turn.prompt}\n\n## Scout\n\n${turn.reply}${turn.template ? `\n\n\`\`\`yaml\n${turn.template}\n\`\`\`` : ''}${turn.adapter ? `\n\n\`\`\`python\n${turn.adapter}\n\`\`\`` : ''}`).join('\n\n')}`;

export async function saveChat(chat: SavedChat): Promise<SavedChat> {
  const { id, title, turns, pinned, archived, revision } = chat;
  return unwrap((await client.put(`/scout/chats/${id}`, { title, turns, pinned, archived, revision })).data);
}
export async function readChat(id: string): Promise<SavedChat> { return unwrap((await client.get(`/scout/chats/${id}`)).data); }
export async function listChats(archived: boolean, offset = 0): Promise<ChatSummary[]> { return unwrap((await client.get('/scout/chats', { params: { archived, offset } })).data); }
export async function branchChat(id: string): Promise<SavedChat> { return unwrap((await client.post(`/scout/chats/${id}/branch`)).data); }
export async function deleteChat(id: string) { await client.delete(`/scout/chats/${id}`); }
export async function createShare(chat: SavedChat, snapshot: ShareSnapshot, audience: 'anyone' | 'invited', emails: string[]): Promise<string> {
  const { id, pinned, archived, revision } = chat;
  const turns = snapshot.turns.map(turn => ({ ...turn, attachments: chat.turns.find(savedTurn => savedTurn.id === turn.id)?.attachments ?? [] }));
  const result = unwrap<{ id: string }>((await client.post(`/scout/chats/${id}/shares`, { audience, emails, snapshot: { title: snapshot.title, turns, pinned, archived, revision } })).data);
  return `${window.location.origin}/share/scout/${result.id}`;
}
