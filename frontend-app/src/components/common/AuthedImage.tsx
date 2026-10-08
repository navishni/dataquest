import { ImageOff } from "lucide-react";
import { useAuthedUrl } from "@/hooks/useAuthedUrl";
import { Skeleton } from "./StateViews";

export function AuthedImage({ src, alt, className = "" }: { src: string; alt: string; className?: string }) {
  const { url, loading, error } = useAuthedUrl(src);
  if (loading) return <Skeleton lines={2} className={className} />;
  if (error || !url) {
    return <div role="img" aria-label={alt} className={`flex items-center gap-2 text-sm text-muted ${className}`}><ImageOff size={16} /> Image unavailable</div>;
  }
  return <img src={url} alt={alt} loading="lazy" className={className} />;
}
