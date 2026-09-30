import { domAnimation } from "motion/react";

/**
 * Motion's animation features (docs/17 §5.5) in a module of their own, so `LazyMotion` loads
 * them as a separate chunk after hydration (`loadMotionFeatures` in ./motion.ts) instead of in
 * the first JavaScript of every console and public page. Only UI that appears after a user
 * action uses `m.*`, so the features are there before they are needed.
 */
export default domAnimation;
