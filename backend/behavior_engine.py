import asyncio
import random
import math
from playwright.async_api import Page, Locator

def _cubic_bezier(t, p0, p1, p2, p3):
    """Calculate a point on a cubic Bezier curve."""
    return (
        (1 - t)**3 * p0 +
        3 * (1 - t)**2 * t * p1 +
        3 * (1 - t) * t**2 * p2 +
        t**3 * p3
    )

class BehaviorEngine:
    def __init__(self, page: Page, behavior: dict = None):
        self.page = page
        # Set defaults if not provided (e.g. older profiles)
        self.behavior = behavior or {
            "typing_speed_wpm": 60,
            "mistake_probability": 0.05,
            "mouse_speed_multiplier": 1.0,
            "reading_speed_wpm": 200,
            "scroll_speed": 250
        }
        
        # We need to track where the mouse is approximately
        self.current_mouse_x = random.randint(100, 500)
        self.current_mouse_y = random.randint(100, 500)

    async def _generate_bezier_points(self, start_x, start_y, end_x, end_y, steps=20):
        """Generates random control points for a cubic bezier to make natural mouse paths."""
        # Random offsets for control points based on distance
        distance = math.hypot(end_x - start_x, end_y - start_y)
        offset = distance * 0.2
        
        cp1_x = start_x + (end_x - start_x) * 0.3 + random.uniform(-offset, offset)
        cp1_y = start_y + (end_y - start_y) * 0.3 + random.uniform(-offset, offset)
        
        cp2_x = start_x + (end_x - start_x) * 0.7 + random.uniform(-offset, offset)
        cp2_y = start_y + (end_y - start_y) * 0.7 + random.uniform(-offset, offset)

        points = []
        for i in range(steps + 1):
            t = i / steps
            # apply easing: easeInOutQuad
            if t < 0.5:
                ease_t = 2 * t * t
            else:
                ease_t = 1 - math.pow(-2 * t + 2, 2) / 2
            
            x = _cubic_bezier(ease_t, start_x, cp1_x, cp2_x, end_x)
            y = _cubic_bezier(ease_t, start_y, cp1_y, cp2_y, end_y)
            points.append((x, y))
            
        return points

    async def human_move(self, target_x: float, target_y: float):
        """Move the mouse to a target x,y using a bezier curve."""
        distance = math.hypot(target_x - self.current_mouse_x, target_y - self.current_mouse_y)
        if distance < 5:
            # Too close, just snap
            await self.page.mouse.move(target_x, target_y)
            self.current_mouse_x, self.current_mouse_y = target_x, target_y
            return

        # Calculate steps based on distance and mouse speed profile
        base_speed = 1000  # pixels per second roughly
        speed = base_speed * self.behavior.get("mouse_speed_multiplier", 1.0)
        duration_sec = distance / speed
        
        # Minimum steps to make it look smooth
        steps = max(10, int(duration_sec * 60)) # assume 60 updates a sec
        
        points = await self._generate_bezier_points(self.current_mouse_x, self.current_mouse_y, target_x, target_y, steps=steps)
        
        step_delay = duration_sec / steps
        
        for x, y in points:
            # Add stochastic jitter
            jitter_x = x + random.uniform(-1, 1)
            jitter_y = y + random.uniform(-1, 1)
            await self.page.mouse.move(jitter_x, jitter_y)
            await asyncio.sleep(step_delay)
            
        self.current_mouse_x, self.current_mouse_y = target_x, target_y

    async def human_click(self, selector: str):
        """Locates an element, human-moves to a random spot inside its bounding box, and clicks."""
        element = self.page.locator(selector).first
        await element.wait_for(state="visible")
        
        box = await element.bounding_box()
        if not box:
            # Fallback to normal click if box isn't found
            await element.click()
            return
            
        # Pick a random point inside the bounding box, slightly padded so we don't hit borders
        pad_x = box["width"] * 0.1
        pad_y = box["height"] * 0.1
        target_x = box["x"] + random.uniform(pad_x, box["width"] - pad_x)
        target_y = box["y"] + random.uniform(pad_y, box["height"] - pad_y)
        
        # Move mouse
        await self.human_move(target_x, target_y)
        
        # Pause before clicking
        await asyncio.sleep(random.uniform(0.1, 0.4))
        
        # Down, pause, Up to simulate physical click
        await self.page.mouse.down()
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await self.page.mouse.up()
        
        # Small chance to move mouse slightly away after clicking
        if random.random() < 0.3:
            await self.human_move(target_x + random.uniform(-50, 50), target_y + random.uniform(-50, 50))

    async def human_type(self, selector: str, text: str):
        """Simulates human typing with variable speeds and realistic typos."""
        # Click the field first
        await self.human_click(selector)
        await asyncio.sleep(random.uniform(0.2, 0.6))
        
        wpm = self.behavior.get("typing_speed_wpm", 60)
        # average word is 5 chars. WPM = chars / 5 / minutes
        cpm = wpm * 5
        cps = cpm / 60
        base_delay = 1.0 / cps
        
        mistake_prob = self.behavior.get("mistake_probability", 0.05)
        
        # Common adjacent keys for typos (simple US layout subset)
        adjacent_keys = {
            'a': ['s', 'q', 'z'], 's': ['a', 'w', 'd', 'x'], 'd': ['s', 'e', 'f', 'c'],
            'e': ['w', 'r', 'd', 's'], 'i': ['u', 'o', 'k'], 'o': ['i', 'p', 'l'],
            't': ['r', 'y', 'g', 'f'], 'n': ['b', 'm', 'j']
        }

        i = 0
        while i < len(text):
            char = text[i]
            
            # Intentionally make a typo?
            if char.lower() in adjacent_keys and random.random() < mistake_prob:
                wrong_char = random.choice(adjacent_keys[char.lower()])
                if char.isupper():
                    wrong_char = wrong_char.upper()
                    
                await self.page.keyboard.press(wrong_char)
                await asyncio.sleep(base_delay * random.uniform(0.8, 1.5))
                
                # Realize the mistake and backspace
                await asyncio.sleep(random.uniform(0.3, 0.8)) # Oh wait, I messed up
                await self.page.keyboard.press("Backspace")
                await asyncio.sleep(base_delay * random.uniform(0.8, 1.2))
                # Next loop iteration will type the correct char since we didn't increment i
            else:
                await self.page.keyboard.press(char)
                i += 1
                
                # Delay
                delay = base_delay * random.uniform(0.5, 1.5)
                # If space, maybe pause slightly longer to think about next word
                if char == ' ' and random.random() < 0.2:
                    delay += random.uniform(0.2, 0.8)
                await asyncio.sleep(delay)

    async def human_scroll(self, pixels: int):
        """Scrolls down smoothly by simulating mouse wheel events."""
        speed = self.behavior.get("scroll_speed", 250) # pixels per chunk
        chunks = int(abs(pixels) / speed)
        if chunks == 0: chunks = 1
        
        chunk_size = pixels / chunks
        
        for _ in range(chunks):
            await self.page.mouse.wheel(0, chunk_size)
            await asyncio.sleep(random.uniform(0.1, 0.3))

    async def human_scroll_to_bottom(self):
        """Scrolls down dynamically until the absolute bottom of the page is reached."""
        last_height = await self.page.evaluate('document.body.scrollHeight')
        same_height_count = 0
        
        while same_height_count < 2:
            # Scroll down a chunk
            await self.human_scroll(random.randint(500, 1000))
            await asyncio.sleep(random.uniform(1.0, 3.0))
            
            new_height = await self.page.evaluate('document.body.scrollHeight')
            if new_height == last_height:
                same_height_count += 1
            else:
                same_height_count = 0
                last_height = new_height

    async def human_click_fallback(self, selectors: list):
        """Tries multiple selectors in order until one is visible and can be clicked."""
        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                # Use a short timeout so we quickly fail over to the next
                await locator.wait_for(state="visible", timeout=2000)
                await self.human_click(selector)
                return True
            except Exception:
                continue
        return False

