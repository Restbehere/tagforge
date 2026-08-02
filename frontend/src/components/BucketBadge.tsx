import { cn } from "@/lib/cn";
import { bucketBadgeClass } from "@/lib/buckets";

export function BucketBadge({
  bucket,
  className,
}: {
  bucket: string;
  className?: string;
}) {
  return (
    <span className={cn("pf-bucket", bucketBadgeClass(bucket), className)}>
      {bucket}
    </span>
  );
}
