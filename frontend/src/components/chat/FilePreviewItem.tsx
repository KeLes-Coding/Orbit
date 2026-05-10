import { FileText, Image, Loader2, X, AlertCircle } from "lucide-react"
import type { ConversationFile } from "@/api/types"

const MAX_PREVIEW_SIZE = 40 * 1024 // 40KB max for client-side image preview

export interface PendingFile {
  file: File
  preview?: string
  status: "pending" | "uploading" | "ready" | "error"
  serverFile?: ConversationFile
  error?: string
}

interface FilePreviewItemProps {
  pendingFile: PendingFile
  onRemove: () => void
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

function FileTypeIcon({ mimeType }: { mimeType: string }) {
  if (mimeType.startsWith("image/")) return <Image className="h-3.5 w-3.5" />
  return <FileText className="h-3.5 w-3.5" />
}

export function FilePreviewItem({ pendingFile, onRemove }: FilePreviewItemProps) {
  const { file, preview, status, serverFile, error } = pendingFile
  const name = serverFile?.original_name || file.name
  const size = serverFile?.file_size || file.size
  const mimeType = serverFile?.file_type || file.type || "application/octet-stream"
  const extractionStatus = serverFile?.extraction_status

  return (
    <div className="file-preview-chip" title={error || undefined}>
      {preview ? (
        <img src={preview} alt={name} className="file-preview-thumb" />
      ) : (
        <span className="file-preview-icon">
          <FileTypeIcon mimeType={mimeType} />
        </span>
      )}
      <span className="file-preview-name">{name}</span>
      <span className="file-preview-size">{formatSize(size)}</span>
      {status === "uploading" && <Loader2 className="h-3 w-3 animate-spin file-preview-status" />}
      {status === "error" && <AlertCircle className="h-3 w-3 file-preview-error" />}
      {status === "ready" && extractionStatus === "processing" && (
        <Loader2 className="h-3 w-3 animate-spin file-preview-status" />
      )}
      <button
        type="button"
        className="file-preview-remove"
        aria-label={`Remove ${name}`}
        onClick={onRemove}
        disabled={status === "uploading"}
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  )
}
