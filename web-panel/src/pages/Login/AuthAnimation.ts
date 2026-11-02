export const AUTH_ANIMATION_PERIOD = 72;
export const AUTH_ANGULAR_SPEED = Math.PI * 2 / AUTH_ANIMATION_PERIOD;
export const AUTH_SCENE_LOOP_PERIOD = AUTH_ANIMATION_PERIOD * 80;
export const AUTH_EARTH_ANGULAR_SPEED = AUTH_ANGULAR_SPEED / 2;
// Eleven orbits per scene loop retain the original 0.012 rad/s speed within 0.01%.
export const AUTH_SMALL_ORBIT_ANGULAR_SPEED = Math.PI * 2 * 11 / AUTH_SCENE_LOOP_PERIOD;
