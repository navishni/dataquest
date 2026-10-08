import { PageHeader } from "@/components/common/Primitives";
import { ChatThread } from "@/components/chat/ChatThread";

export function ChatPage() {
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Ask your data" subtitle="Questions are translated by the backend; every query is checked against your access." />
      <ChatThread />
    </div>
  );
}
