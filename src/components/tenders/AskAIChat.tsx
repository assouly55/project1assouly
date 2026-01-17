import { useState, useRef, useEffect } from 'react';
import { Send, Bot, User, Loader2, AlertCircle, FileText, Clock, MessageCircle, HelpCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { ScrollArea } from '@/components/ui/scroll-area';
import { api, type ClarificationOption } from '@/lib/api';

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'clarification';
  content: string;
  citations?: { document: string; page?: number }[];
  followUpQuestions?: string[];
  responseTimeMs?: number;
  loading?: boolean;
  error?: boolean;
  // For clarification messages
  clarificationPrompt?: string;
  clarificationOptions?: ClarificationOption[];
}

interface AskAIChatProps {
  tenderId: string;
  tenderReference?: string;
}

// Parse citations from AI response text
function parseCitations(text: string): { document: string; section?: string }[] {
  const citations: { document: string; section?: string }[] = [];
  // Match patterns like [Source: CPS, Article 15] or [Source: RC, Section 3.2]
  const pattern = /\[Source:\s*([^,\]]+)(?:,\s*([^\]]+))?\]/gi;
  let match;
  
  while ((match = pattern.exec(text)) !== null) {
    citations.push({
      document: match[1].trim(),
      section: match[2]?.trim()
    });
  }
  
  return citations;
}

// Format AI response with highlighted citations
function formatResponse(text: string): string {
  // Convert citation format to styled spans (will be rendered as HTML)
  return text.replace(
    /\[Source:\s*([^,\]]+)(?:,\s*([^\]]+))?\]/gi,
    '<span class="inline-flex items-center gap-1 px-1.5 py-0.5 bg-primary/10 text-primary rounded text-xs font-medium">$1${2 ? `, $2` : ""}</span>'
  );
}

const SUGGESTED_QUESTIONS = [
  { text: "Quels sont les documents à fournir?", label: "Documents requis" },
  { text: "Quelle est la date limite de soumission?", label: "Délai" },
  { text: "Quel est le montant de la caution provisoire?", label: "Caution" },
  { text: "شنو هي الوثائق اللي خاصني نجيب؟", label: "الوثائق (دارجة)" },
];

export function AskAIChat({ tenderId, tenderReference }: AskAIChatProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const sendMessage = async (question: string) => {
    if (!question.trim() || isLoading) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: question.trim(),
    };

    const assistantPlaceholder: Message = {
      id: (Date.now() + 1).toString(),
      role: 'assistant',
      content: '',
      loading: true,
    };

    setMessages(prev => [...prev, userMessage, assistantPlaceholder]);
    setInput('');
    setIsLoading(true);

    try {
      const response = await api.askAI(tenderId, question.trim());

      if (response.success && response.data) {
        // Check if AI needs clarification
        if (response.data.needs_clarification && response.data.clarification_options) {
          setMessages(prev =>
            prev.map(msg =>
              msg.id === assistantPlaceholder.id
                ? {
                    ...msg,
                    role: 'clarification' as const,
                    content: '',
                    clarificationPrompt: response.data!.clarification_prompt,
                    clarificationOptions: response.data!.clarification_options,
                    responseTimeMs: response.data!.response_time_ms,
                    loading: false,
                  }
                : msg
            )
          );
        } else {
          setMessages(prev =>
            prev.map(msg =>
              msg.id === assistantPlaceholder.id
                ? {
                    ...msg,
                    content: response.data!.answer,
                    citations: response.data!.citations,
                    followUpQuestions: response.data!.follow_up_questions,
                    responseTimeMs: response.data!.response_time_ms,
                    loading: false,
                  }
                : msg
            )
          );
        }
      } else {
        setMessages(prev =>
          prev.map(msg =>
            msg.id === assistantPlaceholder.id
              ? {
                  ...msg,
                  content: response.error || 'Une erreur est survenue',
                  loading: false,
                  error: true,
                }
              : msg
          )
        );
      }
    } catch (err) {
      setMessages(prev =>
        prev.map(msg =>
          msg.id === assistantPlaceholder.id
            ? {
                ...msg,
                content: 'Erreur de connexion au serveur',
                loading: false,
                error: true,
              }
            : msg
        )
      );
    } finally {
      setIsLoading(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage(input);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  const handleSuggestionClick = (question: string) => {
    sendMessage(question);
  };

  return (
    <div className="flex flex-col h-[500px] data-card p-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border bg-muted/30">
        <Bot className="w-5 h-5 text-primary" />
        <div>
          <div className="font-medium text-sm">Assistant Expert</div>
          <div className="text-xs text-muted-foreground">
            Marchés Publics Marocains • {tenderReference || tenderId}
          </div>
        </div>
      </div>

      {/* Messages area */}
      <ScrollArea className="flex-1 p-4">
        {messages.length === 0 ? (
          <div className="space-y-6">
            {/* Welcome message */}
            <div className="text-center py-8">
              <Bot className="w-12 h-12 text-primary mx-auto mb-4 opacity-80" />
              <h3 className="font-medium mb-2">Posez vos questions</h3>
              <p className="text-sm text-muted-foreground max-w-sm mx-auto">
                Je peux répondre en Français, Arabe ou Darija avec des citations précises des documents.
              </p>
            </div>

            {/* Suggested questions */}
            <div className="grid grid-cols-2 gap-2">
              {SUGGESTED_QUESTIONS.map((q, i) => (
                <button
                  key={i}
                  onClick={() => handleSuggestionClick(q.text)}
                  className="text-left p-3 rounded-lg border border-border hover:border-primary/50 hover:bg-primary/5 transition-colors group"
                >
                  <div className="text-xs text-muted-foreground mb-1">{q.label}</div>
                  <div className="text-sm line-clamp-2">{q.text}</div>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map(message => (
              <div
                key={message.id}
                className={`flex gap-3 ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                {(message.role === 'assistant' || message.role === 'clarification') && (
                  <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
                    {message.role === 'clarification' ? (
                      <HelpCircle className="w-4 h-4 text-primary" />
                    ) : (
                      <Bot className="w-4 h-4 text-primary" />
                    )}
                  </div>
                )}

                <div
                  className={`max-w-[80%] rounded-lg px-4 py-3 ${
                    message.role === 'user'
                      ? 'bg-primary text-primary-foreground'
                      : message.error
                      ? 'bg-destructive/10 border border-destructive/20'
                      : message.role === 'clarification'
                      ? 'bg-accent/50 border border-accent'
                      : 'bg-muted'
                  }`}
                >
                  {message.loading ? (
                    <div className="flex items-center gap-2">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span className="text-sm text-muted-foreground">Analyse des documents...</span>
                    </div>
                  ) : message.error ? (
                    <div className="flex items-center gap-2 text-destructive">
                      <AlertCircle className="w-4 h-4" />
                      <span className="text-sm">{message.content}</span>
                    </div>
                  ) : message.role === 'clarification' ? (
                    <div className="space-y-3">
                      <p className="text-sm font-medium">{message.clarificationPrompt}</p>
                      <div className="flex flex-col gap-2">
                        {message.clarificationOptions?.map((option, i) => (
                          <button
                            key={i}
                            onClick={() => sendMessage(option.value)}
                            disabled={isLoading}
                            className="text-left px-4 py-3 rounded-lg border border-primary/30 bg-background hover:bg-primary/10 hover:border-primary/50 transition-all group"
                          >
                            <span className="text-sm">{option.label}</span>
                          </button>
                        ))}
                      </div>
                      {message.responseTimeMs && (
                        <span className="inline-flex items-center gap-1 text-muted-foreground text-xs">
                          <Clock className="w-3 h-3" />
                          {message.responseTimeMs}ms
                        </span>
                      )}
                    </div>
                  ) : message.role === 'user' ? (
                    <div className="text-sm">{message.content}</div>
                  ) : (
                    <div className="space-y-2">
                      <div
                        className="text-sm whitespace-pre-wrap prose prose-sm max-w-none dark:prose-invert"
                        dangerouslySetInnerHTML={{
                          __html: formatResponse(message.content)
                        }}
                      />
                      
                      {/* Citations and response time */}
                      {(message.citations?.length || message.responseTimeMs) && (
                        <div className="pt-2 border-t border-border/50 mt-2 flex flex-wrap items-center gap-2">
                          {message.citations?.slice(0, 5).map((citation, i) => (
                            <span
                              key={i}
                              className="inline-flex items-center gap-1 px-2 py-0.5 bg-background rounded text-xs"
                            >
                              <FileText className="w-3 h-3" />
                              {citation.document}
                              {citation.page && ` p.${citation.page}`}
                            </span>
                          ))}
                          {message.responseTimeMs && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-muted-foreground text-xs ml-auto">
                              <Clock className="w-3 h-3" />
                              {message.responseTimeMs < 1000 
                                ? `${message.responseTimeMs}ms`
                                : `${(message.responseTimeMs / 1000).toFixed(1)}s`}
                            </span>
                          )}
                        </div>
                      )}
                      
                      {/* Follow-up questions */}
                      {message.followUpQuestions && message.followUpQuestions.length > 0 && (
                        <div className="pt-2 mt-2">
                          <div className="flex items-center gap-1 text-xs text-muted-foreground mb-2">
                            <MessageCircle className="w-3 h-3" />
                            Questions suggérées
                          </div>
                          <div className="flex flex-wrap gap-2">
                            {message.followUpQuestions.map((q, i) => (
                              <button
                                key={i}
                                onClick={() => sendMessage(q)}
                                disabled={isLoading}
                                className="text-left px-3 py-1.5 text-xs rounded-full border border-primary/30 text-primary hover:bg-primary/10 transition-colors disabled:opacity-50"
                              >
                                {q}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {message.role === 'user' && (
                  <div className="w-8 h-8 rounded-full bg-secondary flex items-center justify-center flex-shrink-0">
                    <User className="w-4 h-4" />
                  </div>
                )}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </ScrollArea>

      {/* Input area */}
      <form onSubmit={handleSubmit} className="p-4 border-t border-border bg-muted/20">
        <div className="flex gap-2">
          <Textarea
            ref={textareaRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Posez votre question... (FR / AR / Darija)"
            className="min-h-[44px] max-h-32 resize-none"
            disabled={isLoading}
          />
          <Button
            type="submit"
            size="icon"
            disabled={!input.trim() || isLoading}
            className="h-11 w-11 flex-shrink-0"
          >
            {isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground mt-2">
          Appuyez sur Entrée pour envoyer • Shift+Entrée pour nouvelle ligne
        </p>
      </form>
    </div>
  );
}
