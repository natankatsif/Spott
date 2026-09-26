"use client";

import { motion } from "motion/react";
import { useState } from "react";
import { cn } from "@/lib/utils";

interface AnimatedCheckboxProps {
  title?: string;
  defaultChecked?: boolean;
  className?: string;
  onCheckedChange?: (checked: boolean) => void;
  /** project: false = a plain option (no strike-through "done" look), text size from the parent */
  strike?: boolean;
}

const springTransition = {
  type: "spring" as const,
  duration: 0.4,
  bounce: 0.2,
};

export function AnimatedCheckbox({
  title = "Implement Checkbox",
  defaultChecked = false,
  className,
  onCheckedChange,
  strike = true,
}: AnimatedCheckboxProps) {
  const [checked, setChecked] = useState(defaultChecked);

  const handleClick = () => {
    const newChecked = !checked;
    setChecked(newChecked);
    onCheckedChange?.(newChecked);
  };

  return (
    <div
      aria-checked={checked}
      className={cn("flex items-center gap-3 cursor-pointer select-none rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/50", className)}
      onClick={handleClick}
      onKeyDown={(e) => {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          handleClick();
        }
      }}
      role="checkbox"
      tabIndex={0}
    >
      <div
        className={cn(
          "size-4.5 rounded-[6px] flex items-center justify-center border-[1.5px] transition-colors duration-200",
          checked
            ? strike ? "bg-foreground border-transparent" : "bg-brand border-transparent"
            : "bg-transparent border-muted-foreground/40 hover:border-muted-foreground/60"
        )}
      >
        <svg viewBox="0 0 20 20" className="size-full text-background">
          <motion.path
            d="M 0 4.5 L 3.182 8 L 10 0"
            fill="transparent"
            stroke="currentColor"
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeLinejoin="round"
            transform="translate(5 6)"
            initial={{ pathLength: defaultChecked ? 1 : 0, opacity: defaultChecked ? 1 : 0 }}
            animate={{
              pathLength: checked ? 1 : 0,
              opacity: checked ? 1 : 0
            }}
            transition={{
              pathLength: { ease: "easeOut", duration: 0.3 },
              opacity: { duration: 0 }
            }}
          />
        </svg>
      </div>
      <div className="relative">
        <span
          className={cn(
            "font-medium transition-colors duration-200",
            strike ? "text-base" : "text-foreground",
            strike && checked ? "text-muted-foreground" : "text-foreground"
          )}
        >
          {title}
        </span>
        {strike && <motion.div
          className="absolute left-0 top-1/2 h-[1.5px] bg-muted-foreground -translate-y-1/2"
          initial={{ width: defaultChecked ? "100%" : 0, opacity: defaultChecked ? 1 : 0 }}
          animate={{
            width: checked ? "100%" : 0,
            opacity: checked ? 1 : 0,
          }}
          transition={springTransition}
        />}
      </div>
    </div>
  );
}
