/** `whip_down`: a fast camera whip down with motion blur (render/transitions/push_whip.py). */
import { stripFilter } from "./_push";

export const whipDown = stripFilter("whip_down", "down", "whip");

export default whipDown;
