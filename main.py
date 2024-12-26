import pyshark
import openai
import time
import json
from datetime import datetime
from collections import defaultdict
import asyncio
import aiohttp
from typing import Dict, List, Optional
import logging
from dataclasses import dataclass, field
from enum import Enum

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PoetryStyle(Enum):
    PESSOA = "pessoa"
    WHITMAN = "whitman"
    DICKINSON = "dickinson"

@dataclass
class PacketData:
    src_ip: str
    dest_ip: str
    protocol: str
    length: int
    timestamp: float
    port_src: Optional[int] = None
    port_dst: Optional[int] = None
    flags: Optional[str] = None

@dataclass
class PoetryArchiveEntry:
    poetry: str
    packets: List[dict]
    style: str
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())

class NetworkPoetryGenerator:
    def __init__(self, interface: str, openai_api_key: str):
        """
        Initialize the network poetry generator with:
          - A pyshark LiveCapture instance for packet sniffing.
          - An OpenAI client configured with the provided API key.
          - Internal buffers and structures for storing packets and generated poems.
        """
        self.interface = interface
        self.capture = pyshark.LiveCapture(interface=interface)
        self.openai_client = openai.Client(api_key=openai_api_key)
        self.packet_buffer: List[PacketData] = []
        self.poetry_archive: Dict[str, PoetryArchiveEntry] = defaultdict()

    def extract_packet_data(self, packet) -> Optional[PacketData]:
        """
        Extract relevant details from a pyshark packet object.
        Return None if extraction fails (to gracefully handle anomalies).
        """
        try:
            # Safely get IP and transport details if they exist
            src_ip = packet.ip.src if hasattr(packet, 'ip') else "Unknown"
            dest_ip = packet.ip.dst if hasattr(packet, 'ip') else "Unknown"
            protocol = packet.transport_layer if hasattr(packet, 'transport_layer') else "Unknown"
            length = int(packet.length)
            timestamp = time.time()

            port_src = None
            port_dst = None
            flags = None

            if protocol != "Unknown":
                port_src = int(packet[protocol].srcport)
                port_dst = int(packet[protocol].dstport)
                # Some protocols might not have flags
                flags = getattr(packet[protocol], 'flags', None)

            return PacketData(
                src_ip=src_ip,
                dest_ip=dest_ip,
                protocol=protocol,
                length=length,
                timestamp=timestamp,
                port_src=port_src,
                port_dst=port_dst,
                flags=flags
            )
        except Exception as e:
            logger.error(f"Error extracting packet data: {e}")
            return None

    def craft_prompt(self, packets: List[PacketData], style: PoetryStyle) -> str:
        """
        Build the text prompt sent to OpenAI. 
        Each packet is described, and the style context (Pessoa, Whitman, or Dickinson) is included.
        """
        base_context = {
            PoetryStyle.PESSOA: """
                Channel the introspective, philosophical voice of Fernando Pessoa's heteronyms.
                Contemplate each packet as a fleeting moment of consciousness traversing the ether.
                Reflect on the metaphysical nature of data moving through intangible spaces.
            """,
            PoetryStyle.WHITMAN: """
                Embrace Walt Whitman's grand, expansive style.
                Treat each packet as part of a cosmic tapestry of modern life.
                Weave the digital flow into humanity's universal song.
            """,
            PoetryStyle.DICKINSON: """
                Employ Emily Dickinson's delicate yet potent verse.
                Observe the micro-moments of transmission with a keen, almost reverent eye.
                Harness unusual punctuation and subtle metaphor to illuminate digital rhythms.
            """
        }

        # Turn each packet into a short descriptive line
        packet_descriptions = []
        for packet in packets:
            description = (
                f"Data from {packet.src_ip}:{packet.port_src} "
                f"to {packet.dest_ip}:{packet.port_dst}, "
                f"{packet.length} bytes via {packet.protocol}."
            )
            packet_descriptions.append(description)

        return f"""
        {base_context[style]}

        Consider the following network movements:
        {chr(10).join(packet_descriptions)}

        Transform these digital flows into a poem in the style of {style.value}.
        Contemplate the symbolic meaning of packets dancing between nodes and
        the resonance of ephemeral data in our digital consciousness.
        """

    async def generate_poetry(self, prompt: str) -> str:
        """
        Send the prompt to OpenAI and retrieve the text generated.
        The result is truncated or returned as-is if there's an error.
        """
        try:
            response = await asyncio.to_thread(
                lambda: self.openai_client.completions.create(
                    model="gpt-4",
                    prompt=prompt,
                    max_tokens=200,
                    temperature=0.9
                )
            )
            return response.choices[0].text.strip()
        except Exception as e:
            logger.error(f"Error generating poetry: {e}")
            return "Error generating poetic response"

    async def process_packets(self, style: PoetryStyle = PoetryStyle.PESSOA):
        """
        Start sniffing packets in real-time, accumulate them in a buffer,
        and periodically transform them into poems when the threshold is met.
        """
        async def buffer_processor():
            while True:
                # When we've got a chunk of packets, build a poem
                if len(self.packet_buffer) >= 5:
                    prompt = self.craft_prompt(self.packet_buffer, style)
                    poetry = await self.generate_poetry(prompt)

                    archive_entry = PoetryArchiveEntry(
                        poetry=poetry,
                        packets=[vars(p) for p in self.packet_buffer],
                        style=style.value
                    )

                    # Use the timestamp as a dict key
                    self.poetry_archive[archive_entry.generated_at] = archive_entry

                    # Clear the current buffer
                    self.packet_buffer.clear()

                    logger.info(
                        f"\n--- New Poetry Generated at {archive_entry.generated_at} ---\n{poetry}\n"
                    )

                await asyncio.sleep(1)  # Sleep briefly to avoid hammering

        # Spin up the asynchronous task that inspects the buffer
        buffer_processor_task = asyncio.create_task(buffer_processor())

        # Main packet-capturing loop
        try:
            for packet in self.capture.sniff_continuously():
                packet_data = self.extract_packet_data(packet)
                if packet_data:
                    self.packet_buffer.append(packet_data)
                await asyncio.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("Stopping packet capture...")
            buffer_processor_task.cancel()
        finally:
            # Save any final results to disk before exiting
            self.save_archive()

    def save_archive(self):
        """
        Store the entire poetry archive to a JSON file for future perusal.
        """
        try:
            # Convert dataclass objects to simple dictionaries
            archive_dict = {
                timestamp: {
                    'poetry': entry.poetry,
                    'packets': entry.packets,
                    'style': entry.style,
                    'generated_at': entry.generated_at
                }
                for timestamp, entry in self.poetry_archive.items()
            }

            with open('network_poetry_archive.json', 'w') as f:
                json.dump(archive_dict, f, indent=2)

            logger.info("Poetry archive saved successfully.")
        except Exception as e:
            logger.error(f"Error saving poetry archive: {e}")

async def main():
    generator = NetworkPoetryGenerator(
        interface='eth0',
        openai_api_key="YOUR_OPENAI_API_KEY"
    )
    await generator.process_packets()

if __name__ == "__main__":
    asyncio.run(main())
