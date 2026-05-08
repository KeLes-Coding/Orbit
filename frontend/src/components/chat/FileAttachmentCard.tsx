import { FileText, Image, Download } from "lucide-react"
import type { ContentPart } from "@/api/types"
import { fileApi } from "@/api/files"

interface FileAttachmentCardProps {
  file: Extract<ContentPart, { type: "file" }>
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

export function FileAttachmentCard({ file }: FileAttachmentCardProps) {
  const downloadUrl = fileApi.getContentUrl(file.file_id)
  const isImage = file.mime_type.startsWith("image/")

  if (isImage) {
    return (
      <a
        href={downloadUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="file-attachment-card file-attachment-image"
      >
        <img
          src={downloadUrl}
          alt={file.name}
          className="file-attachment-thumbnail"
          loading="lazy"
        />
        <div className="file-attachment-info">
          <span className="file-attachment-name">{file.name}</span>
          <span className="file-attachment-size">{formatSize(file.file_size)}</span>
        </div>
        <Download className="h-4 w-4 file-attachment-download" />
      </a>
    )
  }

  return (
    <a
      href={downloadUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="file-attachment-card file-attachment-document"
    >
      <span className="file-attachment-doc-icon">
        <FileText className="h-5 w-5" />
      </span>
      <div className="file-attachment-info">
        <span className="file-attachment-name">{file.name}</span>
        <span className="file-attachment-size">{formatSize(file.file_size)}</span>
      </div>
      <Download className="h-4 w-4 file-attachment-download" />
    </a>
  )
}

export function FileAttachmentList({ contentParts }: { contentParts: unknown[] }) {
  const files = (contentParts || []).filter(
    (part): part is Extract<ContentPart, { type: "file" }> =>
      typeof part === "object" && part !== null && (part as ContentPart).type === "file",
  )
  if (files.length === 0) return null

  return (
    <div className="file-attachment-list">
      {files.map((file) => (
        <FileAttachmentCard key={file.file_id} file={file} />
      ))}
    </div>
  )
}
