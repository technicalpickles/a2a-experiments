import { forwardRef } from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/utils'

const buttonVariants = cva(
  'inline-flex cursor-pointer items-center justify-center gap-1.5 whitespace-nowrap rounded-sm border font-bold tracking-[.06em] transition-colors duration-[120ms] ease-linear disabled:pointer-events-none',
  {
    variants: {
      variant: {
        primary:
          'border-transparent bg-primary text-primary-foreground hover:bg-primary-hover active:bg-primary-active disabled:bg-[oklch(0.26_0.02_150)] disabled:text-[oklch(0.50_0.02_150)]',
        secondary:
          'border-border bg-transparent text-foreground hover:bg-accent active:bg-muted disabled:text-muted-foreground',
        deny:
          'border-[oklch(0.55_0.20_340)] bg-transparent text-destructive hover:bg-destructive/10 active:bg-destructive/25 dark:hover:border-[oklch(0.68_0.22_340)] dark:hover:bg-[oklch(0.22_0.08_340)] dark:active:bg-[oklch(0.34_0.14_340)] dark:active:text-white',
        ghost:
          'border-transparent bg-transparent text-primary hover:bg-accent active:bg-muted',
      },
      size: {
        sm: 'h-7 px-2.5 text-[11.5px]',
        md: 'h-8 px-3.5 text-[12px] tracking-[.08em]',
        lg: 'h-11 w-full text-[12px] tracking-[.08em]',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'sm' },
  },
)

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants>

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  ),
)
Button.displayName = 'Button'

export { Button }
