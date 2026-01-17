import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { Tender, TenderSearchParams } from '@/types/tender';

export function useTenders(params: TenderSearchParams = {}) {
  return useQuery({
    queryKey: ['tenders', params],
    queryFn: async () => {
      const result = await api.listTenders(params);
      if (!result.success) {
        throw new Error(result.error);
      }
      return result.data;
    },
    refetchInterval: 30000, // Refetch every 30s
  });
}

export function useTender(id: string) {
  return useQuery({
    queryKey: ['tender', id],
    queryFn: async () => {
      const result = await api.getTender(id);
      if (!result.success) {
        throw new Error(result.error);
      }
      return result.data;
    },
    enabled: !!id,
  });
}

export function useAnalyzeTender() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: async (id: string) => {
      const result = await api.analyzeTender(id);
      if (!result.success) {
        throw new Error(result.error);
      }
      return result.data;
    },
    onSuccess: (data, id) => {
      queryClient.invalidateQueries({ queryKey: ['tender', id] });
      queryClient.invalidateQueries({ queryKey: ['tenders'] });
    },
  });
}

export function useAskAI(tenderId: string) {
  return useMutation({
    mutationFn: async (question: string) => {
      const result = await api.askAI(tenderId, question);
      if (!result.success) {
        throw new Error(result.error);
      }
      return result.data;
    },
  });
}

export function useBackendHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: async () => {
      const result = await api.healthCheck();
      return result.success;
    },
    refetchInterval: 10000, // Check every 10s
    retry: false,
  });
}
