import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/**
 * The assistant's mark (public/logos/logo-center.svg). Painted with `currentColor`, so the colour comes from
 * the parent: `<LogoMark className="text-brand" />`, or `color` prop. Default is the design's #7EB6E3.
 */
export function LogoMark({ className, color, ...props }: ComponentProps<"svg">) {
  return (
    <svg
      aria-hidden
      className={cn("text-[#7EB6E3] drop-shadow-[0_6px_8px_rgb(0_0_0/0.1)]", className)}
      fill="currentColor"
      style={color ? { color } : undefined}
      viewBox="0 0 79 87"
      xmlns="http://www.w3.org/2000/svg"
      {...props}
    >
      <path d="M28.5993 35.788H2.57409C1.15246 35.788 0 36.9289 0 38.3362V58.6178C0 60.0251 1.15246 61.166 2.57409 61.166H28.5993C30.021 61.166 31.1734 60.0251 31.1734 58.6178V38.3362C31.1734 36.9289 30.021 35.788 28.5993 35.788Z" />
      <path d="M43.391 86.0514C45.0124 87.6567 47.785 86.5197 47.785 84.2495V53.5631C47.785 52.1556 46.6326 51.0149 45.2109 51.0149H14.2125C11.9193 51.0149 10.7708 53.7595 12.3924 55.3649L43.391 86.0514Z" />
      <path d="M50.3592 50.9132H76.3844C77.806 50.9132 78.9585 49.7723 78.9585 48.365V28.0834C78.9585 26.6761 77.806 25.5353 76.3844 25.5353L50.3592 25.5353C48.9375 25.5353 47.7851 26.6761 47.7851 28.0834V48.365C47.7851 49.7723 48.9375 50.9132 50.3592 50.9132Z" />
      <path d="M35.5676 0.751464C33.9459 -0.853821 31.1733 0.283106 31.1733 2.55329V33.2398C31.1733 34.6472 32.3257 35.788 33.7474 35.788H64.7458C67.039 35.788 68.1874 33.0433 66.566 31.4379L35.5676 0.751464Z" />
    </svg>
  );
}
