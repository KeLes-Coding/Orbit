import { useCallback, useState } from "react"
import { fileApi } from "@/api/files"
import type { PendingFile } from "@/components/chat/FilePreviewItem"

export function useFileUpload() {
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([])
  const [isUploadingFiles, setIsUploadingFiles] = useState(false)

  const addFiles = useCallback((files: File[]) => {
    const newFiles: PendingFile[] = files.map((file) => {
      const isImage = file.type.startsWith("image/")
      return {
        file,
        status: "pending" as const,
        preview: isImage ? URL.createObjectURL(file) : undefined,
      }
    })
    setPendingFiles((prev) => [...prev, ...newFiles])
  }, [])

  const removeFile = useCallback((index: number) => {
    setPendingFiles((prev) => {
      const next = [...prev]
      const removed = next[index]
      if (removed?.preview) URL.revokeObjectURL(removed.preview)
      next.splice(index, 1)
      return next
    })
  }, [])

  const uploadPendingFiles = useCallback(
    async (conversationId: string | null): Promise<string[]> => {
      const files = pendingFiles.filter((pf) => pf.status !== "ready")
      if (files.length === 0) {
        return pendingFiles
          .filter((pf) => pf.serverFile?.id)
          .map((pf) => pf.serverFile!.id)
      }

      setIsUploadingFiles(true)
      const fileIds: string[] = []
      const updated: PendingFile[] = [...pendingFiles]

      for (let i = 0; i < updated.length; i++) {
        const pf = updated[i]
        if (pf.status === "ready" && pf.serverFile?.id) {
          fileIds.push(pf.serverFile.id)
          continue
        }
        updated[i] = { ...pf, status: "uploading" }
        setPendingFiles([...updated])

        try {
          let serverFile
          if (conversationId) {
            serverFile = await fileApi.uploadToConversation(conversationId, pf.file)
          } else {
            serverFile = await fileApi.uploadPending(pf.file)
          }
          fileIds.push(serverFile.id)
          updated[i] = { ...pf, status: "ready", serverFile }
        } catch (err) {
          updated[i] = {
            ...pf,
            status: "error",
            error: err instanceof Error ? err.message : "Upload failed",
          }
        }
        setPendingFiles([...updated])
      }

      setIsUploadingFiles(false)
      return fileIds
    },
    [pendingFiles],
  )

  const clearPendingFiles = useCallback(() => setPendingFiles([]), [])

  return {
    pendingFiles,
    isUploadingFiles,
    addFiles,
    removeFile,
    uploadPendingFiles,
    clearPendingFiles,
  }
}
