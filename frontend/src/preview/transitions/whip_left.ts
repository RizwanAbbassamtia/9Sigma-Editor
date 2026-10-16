/** `whip_left`: a fast camera whip left with motion blur (render/transitions/push_whip.py). */
import { stripFilter } from "./_push";

export const whipLeft = stripFilter("whip_left", "left", "whip");

export default whipLeft;
