// React wrapper for the follow-along model.
// Usage: <FollowAvatarView exercise="wand_er" affectedSide="left" target={45} liveAngle={imuAngle} onRep={n => ...} />
import { useEffect, useRef } from 'react'
import { createFollowAvatar, type AvatarOptions, type FollowAvatar, type Phase } from './FollowAvatar'

export interface FollowAvatarViewProps extends AvatarOptions {
  /** Patient's live angle (from the band or camera) shown on the arc; null hides it. */
  liveAngle?: number | null
  onAngle?: (deg: number) => void
  onPhase?: (phase: Phase) => void
  onRep?: (count: number) => void
  className?: string
  /** Accessible description of what the model is demonstrating. */
  label?: string
}

export function FollowAvatarView(props: FollowAvatarViewProps) {
  const host = useRef<HTMLDivElement>(null)
  const avatar = useRef<FollowAvatar | null>(null)
  const cbs = useRef(props)
  cbs.current = props

  useEffect(() => {
    if (!host.current) return
    const a = createFollowAvatar(host.current, props)
    avatar.current = a
    const offs = [
      a.on('angle', (d) => cbs.current.onAngle?.(d)),
      a.on('phase', (p) => cbs.current.onPhase?.(p)),
      a.on('rep', (n) => cbs.current.onRep?.(n)),
    ]
    return () => { offs.forEach((off) => off()); a.dispose(); avatar.current = null }
    // create once; later prop changes go through setOptions below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    avatar.current?.setOptions({
      exercise: props.exercise, affectedSide: props.affectedSide, target: props.target,
      holdS: props.holdS, restS: props.restS, speedDegPerS: props.speedDegPerS,
      mirror: props.mirror, view: props.view, showArc: props.showArc, playing: props.playing,
    })
  }, [props.exercise, props.affectedSide, props.target, props.holdS, props.restS, props.speedDegPerS,
      props.mirror, props.view, props.showArc, props.playing])

  useEffect(() => { avatar.current?.setLiveAngle(props.liveAngle ?? null) }, [props.liveAngle])

  return <div ref={host} className={props.className} role="img" aria-label={props.label ?? 'Exercise demonstration'}
              style={{ width: '100%', height: '100%', minHeight: 320 }} />
}
