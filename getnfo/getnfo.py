import os
import discord
import asyncio
import subprocess
import requests
from redbot.core import commands
from discord.ui import View, Button
from discord import app_commands
import json
import logging
import random
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s')


class getnfo(commands.Cog):
    """Cog to fetch NFOs for warez releases using the xrel.to, predb.net and crowdnfo.net APIs"""

    def __init__(self, bot):
        self.bot = bot
        self.client_id, self.client_secret = self.load_credentials()
        self.xrel_api_base_url = "https://api.xrel.to/v2"
        self.srrdb_api_base_url = "https://api.srrdb.com/v1/nfo/"
        self.crowdnfo_api_base_url = "https://crowdnfo.net/api"
        self.token = None
        self.token_expires_at = 0  # Timestamp when the token expires
        self.bot.loop.create_task(self.schedule_token_refresh())  # Schedule token refresh
        self.no_release_found_message = (
            "```Arrr! ⚓️ Kein Release im sichtbaren Horizont, mein Freund! 🏴‍☠️ Versuche es doch mal "
            "mit einem anderen Suchbegriff oder check die Crew von einer anderen Release-Group. "
            "Vielleicht ist FuN an Bord!? 😆```")
        self.no_release_found_message_easter_egg = ("```Ey, was los? Kein Release gefunden, du Opfer! Wahrscheinlich "
                                                    "haste wieder irgendwas falsch gemacht, du Kiosk-König. Guck "
                                                    "nochmal richtig oder lass es einfach – Nuttööö!```")

    @commands.command()
    async def sync_slash(self, ctx):
        await self.bot.tree.sync()
        await ctx.message.add_reaction("✅")


    @commands.hybrid_command(name="mediainfo", description="Fetch MediaInfo via crowdNFO")
    @app_commands.describe(release="Release name")
    async def mediainfo(self, ctx, *, release: str):
        """Fetch MediaInfo from crowdnfo.net"""
        await ctx.typing()
        
        # Fetch MediaInfo specifically without fallback
        url = f"{self.crowdnfo_api_base_url}/releases/{release}/files/best"
        params = {
            "type": "MediaInfo",
            "fallback": "false"
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                mediainfo_data = response.json()
                
                # Create embed with MediaInfo
                embed = discord.Embed(
                    title=f"{release}",
                    color=discord.Color.from_rgb(41, 134, 204)
                )
                
                # Format General information
                general_info = []
                if mediainfo_data.get('fileSize'):
                    general_info.append(f"File Size: {self.format_file_size(mediainfo_data['fileSize'])}")
                if mediainfo_data.get('duration'):
                    general_info.append(f"Duration: {self.format_duration(mediainfo_data['duration'])}")
                
                if general_info:
                    embed.add_field(name="General", value="\n".join(general_info), inline=False)
                
                # Format Video information
                video_info = []
                if mediainfo_data.get('videoResolution'):
                    video_info.append(f"Resolution: {mediainfo_data['videoResolution']}")
                if mediainfo_data.get('videoCodec'):
                    video_info.append(f"Codec: {mediainfo_data['videoCodec']}")
                if mediainfo_data.get('videoBitRate'):
                    video_info.append(f"Bitrate: {self.format_bitrate(mediainfo_data['videoBitRate'])}")
                if mediainfo_data.get('videoFrameRate'):
                    video_info.append(f"Frame Rate: {mediainfo_data['videoFrameRate']} FPS")
                if mediainfo_data.get('videoBitDepth'):
                    video_info.append(f"Bit Depth: {mediainfo_data['videoBitDepth']} Bit")
                
                if video_info:
                    embed.add_field(name="Video", value="\n".join(video_info), inline=False)
                
                # Format Audio information
                audio_tracks = mediainfo_data.get('audioTracks', [])
                if audio_tracks:
                    audio_info = []
                    for track in audio_tracks:
                        track_info = []
                        if track.get('language'):
                            track_info.append(track['language'])
                        if track.get('codec'):
                            track_info.append(track['codec'])
                        if track.get('channels'):
                            track_info.append(f"{track['channels']}ch")
                        if track.get('bitRate'):
                            track_info.append(f"@{self.format_bitrate(track['bitRate'])}")
                        if track.get('isDefault'):
                            track_info.append("Default")
                        
                        audio_info.append(" ".join(track_info))
                    
                    embed.add_field(name="Audio", value="\n".join(audio_info), inline=False)
                
                # Format Subtitle information
                subtitle_tracks = mediainfo_data.get('subtitleTracks', [])
                if subtitle_tracks:
                    subtitle_info = []
                    for track in subtitle_tracks:
                        track_info = []
                        if track.get('language'):
                            track_info.append(track['language'])
                        if track.get('forced'):
                            track_info.append("Forced")
                        if track.get('format'):
                            track_info.append(track['format'])
                        if track.get('isDefault'):
                            track_info.append("Default")
                        
                        subtitle_info.append(" ".join(track_info))
                    
                    embed.add_field(name="Subtitles", value="\n".join(subtitle_info), inline=False)
                
                # Add source field
                embed.add_field(name="Source", value="[crowdNFO](https://crowdnfo.net/)", inline=False)
                
                # Create button
                release_id = mediainfo_data.get('releaseId')
                button = Button(label="View on crowdNFO", url=f"https://crowdnfo.net/release/{release_id}")
                view = View()
                view.add_item(button)
                
                await ctx.send(embed=embed, view=view)
            else:
                await ctx.send("No MediaInfo found for this release on crowdNFO.")
        except Exception as e:
            logging.error(f"Error fetching MediaInfo: {e}")
            await ctx.send("An error occurred while fetching MediaInfo.")


    @commands.hybrid_command(name="nfo", description="Fetch NFO via xREL/srrDB/crowdNFO")
    @app_commands.describe(release="Release name")
    async def nfo(self, ctx, *, release: str):
        await ctx.typing()
        api_responses = await self.fetch_responses(ctx, release)
        await self.send_nfo(ctx, api_responses, release)

    async def fetch_responses(self, ctx, release):
        responses = {
            'srrdb': await self.fetch_srrdb_response(ctx, release),
            'xrel': await self.fetch_xrel_response(ctx, release),
            'crowdnfo': await self.fetch_crowdnfo_response(ctx, release)
        }
        return responses

    async def fetch_srrdb_response(self, ctx, release):
        url = f"{self.srrdb_api_base_url}{release}"

        response = requests.get(url)

        if response.status_code == 200 and response.json()['release'] is None or response.status_code != 200:
            return {
                'success': None,
                'button': False
            }

        button = Button(label="View on srrDB", url=f"https://www.srrdb.com/release/details/{release}")

        return {
            'success': True,
            'button': button
        }

    async def fetch_xrel_response(self, ctx, release):
        token = await self.get_token()

        if not token:
            await ctx.send("Failed to obtain valid authentication token.")
            return

        for type_path, nfo_type in [("/release/info.json", "release"), ("/p2p/rls_info.json", "p2p_rls")]:
            url = self.xrel_api_base_url + type_path
            curl_command = ["curl", "-s", "-H", f"Authorization: Bearer {token}", "-G", url, "--data-urlencode",
                            f"dirname={release}"]
            response = subprocess.run(curl_command, capture_output=True)

            if response.returncode == 0:
                try:
                    release_info = json.loads(response.stdout.decode('utf-8'))
                    if "ext_info" in release_info and "link_href" in release_info["ext_info"]:
                        release_url = release_info["link_href"]
                        button = Button(label="View on xREL", url=release_url)
                        return {
                            'success': True,
                            'button': button,
                            'data': {
                                'release_url': release_url,
                                'release_info': release_info,
                                'nfo_type': nfo_type,
                            }
                        }
                except json.JSONDecodeError:
                    continue
        return {
            'success': False,
            'button': None
        }

    async def fetch_crowdnfo_response(self, ctx, release):
        """Fetch NFO or MediaInfo from crowdnfo.net API"""
        url = f"{self.crowdnfo_api_base_url}/releases/{release}/files/best"
        params = {
            "type": "NFO",
            "fallback": "true"
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                release_id = data.get('releaseId')
                file_type = data.get('fileType')
                button = Button(label="View on crowdNFO", url=f"https://crowdnfo.net/release/{release_id}")
                
                return {
                    'success': True,
                    'fileType': file_type,
                    'releaseId': release_id,
                    'data': data,
                    'button': button
                }
        except Exception as e:
            logging.error(f"Error fetching from crowdnfo: {e}")
        
        return {
            'success': False,
            'fileType': None,
            'releaseId': None,
            'data': None,
            'button': None
        }

    async def send_nfo(self, ctx, api_responses, release):
        # Check if any service has NFO
        has_nfo = (
            api_responses['srrdb']['success'] or
            (api_responses['xrel']['success'] and api_responses['xrel']['data']) or
            (api_responses['crowdnfo']['success'] and api_responses['crowdnfo']['fileType'] == 'NFO')
        )
        
        if has_nfo:
            if api_responses['srrdb']['success']:
                await self.send_srrdb_nfo(ctx, api_responses, release)
            elif api_responses['xrel']['success']:
                await self.send_xrel_nfo(ctx, api_responses, release)
            elif api_responses['crowdnfo']['success'] and api_responses['crowdnfo']['fileType'] == 'NFO':
                await self.send_crowdnfo_nfo(ctx, api_responses, release)
        elif api_responses['crowdnfo']['success'] and api_responses['crowdnfo']['fileType'] == 'MediaInfo':
            await self.send_crowdnfo_mediainfo(ctx, api_responses, release)
        else:
            chance = random.randint(1, 100)
            if chance <= 10:
                await ctx.send(self.no_release_found_message_easter_egg)
            else:
                await ctx.send(self.no_release_found_message)
            return

    async def send_xrel_nfo(self, ctx, api_responses, release):
        data = api_responses['xrel']['data']
        headers = {"Authorization": f"Bearer {await self.get_token()}"}
        nfo_url = f"{self.xrel_api_base_url}/nfo/{data['nfo_type']}.json"

        curl_command = [
            "curl", "-s",
            "-H", f"Authorization: {headers['Authorization']}",
            "-G", nfo_url,
            "--data-urlencode", f"id={data['release_info']['id']}"
        ]

        log_command = ' '.join(curl_command)
        logging.debug(f"Curl command: {log_command}")

        response = subprocess.run(curl_command, capture_output=True)
        nfo_response_content = response.stdout

        if response.returncode == 0 and nfo_response_content:
            try:
                view = View()
                if api_responses['srrdb']['button']:
                    view.add_item(api_responses['srrdb']['button'])
                view.add_item(api_responses['xrel']['button'])
                if api_responses['crowdnfo']['button']:
                    view.add_item(api_responses['crowdnfo']['button'])

                file_name = f"{data['release_info']['id']}_nfo"
                file_path = f"/tmp/{file_name}.png"

                with open(file_path, "wb") as temp_file:
                    temp_file.write(nfo_response_content)

                if data['nfo_type'] == 'p2p_rls':
                    release_type = 'P2P'
                    color = discord.Color.from_rgb(41, 134, 204)
                else:
                    release_type = "scene"
                    color = discord.Color.from_rgb(244, 67, 54)

                comments = await self.fetch_comments(release, data)

                await self.send_embed_with_image(ctx, file_path.replace(".png", ""),
                                                 release,
                                                 view,
                                                 source="[xREL](https://www.xrel.to/)",
                                                 release_type=release_type,
                                                 color=color,
                                                 comments=comments
                                                 )

                os.remove(file_path)
            except Exception as e:
                logging.error(f"Failed to process NFO response: {e}")
                await ctx.send("Failed to process NFO response.")

    async def send_srrdb_nfo(self, ctx, api_responses, release):
        url = f"https://api.srrdb.com/v1/nfo/{release}"

        response = requests.get(url)

        if response.status_code == 200:
            if response.json()['release'] is None:
                return

            nfo_response = requests.get(response.json()['nfolink'][0])
            current_directory = os.path.dirname(os.path.abspath(__file__))
            file_name = release
            file_path = os.path.join(current_directory, file_name)

            with open(file_path + '.nfo', "wb") as file:
                file.write(nfo_response.content)

            infekt_exe = os.path.join(current_directory, "iNFEKT", "infekt-cli")
            nfo_file_path = os.path.join(current_directory, f"{file_name}")

            flags_and_arguments = [
                '--png', nfo_file_path + '.nfo',
                '-W', '15',
                '-H', '25',
                '-R', '15',
                '-G', '808080'
            ]

            try:
                result = subprocess.run([infekt_exe] + flags_and_arguments, capture_output=True, text=True)

                print("Return code:", result.returncode)
                print("Default output:", result.stdout)
                print("Error output:", result.stderr)
            except Exception as e:
                print(f"Error occurred: {e}")

            view = View()
            view.add_item(api_responses['srrdb']['button'])
            comments = 0
            if api_responses['xrel']['button']:
                comments = await self.fetch_comments(release, api_responses['xrel']['data'])
                view.add_item(api_responses['xrel']['button'])
            if api_responses['crowdnfo']['button']:
                view.add_item(api_responses['crowdnfo']['button'])

            await self.send_embed_with_image(ctx,
                                             file_path,
                                             file_name,
                                             view,
                                             source="[srrDB](https://www.srrdb.com/)",
                                             release_type="Scene",
                                             color=discord.Color.from_rgb(244, 67, 54),
                                             comments=comments
                                             )

            os.remove(nfo_file_path + '.nfo')
            os.remove(nfo_file_path + '.png')

    async def send_crowdnfo_nfo(self, ctx, api_responses, release):
        """Send NFO from crowdnfo.net"""
        url = f"{self.crowdnfo_api_base_url}/releases/{release}/files/best"
        params = {
            "type": "NFO",
            "fallback": "true",
            "raw": "true"
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                nfo_content = response.text
                
                # Save NFO to file
                current_directory = os.path.dirname(os.path.abspath(__file__))
                file_name = release
                file_path = os.path.join(current_directory, file_name)
                
                with open(file_path + '.nfo', "w", encoding='utf-8') as file:
                    file.write(nfo_content)
                
                # Convert to image using infekt
                infekt_exe = os.path.join(current_directory, "iNFEKT", "infekt-cli")
                nfo_file_path = os.path.join(current_directory, f"{file_name}")

                flags_and_arguments = [
                    '--png', nfo_file_path + '.nfo',
                    '-W', '15',
                    '-H', '25',
                    '-R', '15',
                    '-G', '808080'
                ]

                try:
                    result = subprocess.run([infekt_exe] + flags_and_arguments, capture_output=True, text=True)
                    print("Return code:", result.returncode)
                    print("Default output:", result.stdout)
                    print("Error output:", result.stderr)
                except Exception as e:
                    print(f"Error occurred: {e}")
                    return
                
                # Create view with buttons
                view = View()
                view.add_item(api_responses['crowdnfo']['button'])
                if api_responses['srrdb']['button']:
                    view.add_item(api_responses['srrdb']['button'])
                if api_responses['xrel']['button']:
                    view.add_item(api_responses['xrel']['button'])
                
                # Get comments count
                comments = f"[0](https://crowdnfo.net/release/{api_responses['crowdnfo']['releaseId']})"
                
                await self.send_embed_with_image(ctx,
                                                file_path,
                                                file_name,
                                                view,
                                                source="[crowdNFO](https://crowdnfo.net/)",
                                                release_type="Scene",
                                                color=discord.Color.from_rgb(244, 67, 54),
                                                comments=comments
                                                )
                
                # Clean up
                os.remove(nfo_file_path + '.nfo')
                os.remove(nfo_file_path + '.png')
                
        except Exception as e:
            logging.error(f"Error processing crowdnfo NFO: {e}")
            await ctx.send("Failed to process crowdNFO NFO.")

    async def send_crowdnfo_mediainfo(self, ctx, api_responses, release):
        """Send MediaInfo from crowdnfo.net in a formatted embed"""
        mediainfo_data = api_responses['crowdnfo']['data']
        
        # Create embed
        embed = discord.Embed(
            title=f"{release}",
            color=discord.Color.from_rgb(41, 134, 204)
        )
        
        # Format General information
        general_info = []
        if mediainfo_data.get('fileSize'):
            general_info.append(f"File Size: {self.format_file_size(mediainfo_data['fileSize'])}")
        if mediainfo_data.get('duration'):
            general_info.append(f"Duration: {self.format_duration(mediainfo_data['duration'])}")
        
        if general_info:
            embed.add_field(name="General", value="\n".join(general_info), inline=False)
        
        # Format Video information
        video_info = []
        if mediainfo_data.get('videoResolution'):
            video_info.append(f"Resolution: {mediainfo_data['videoResolution']}")
        if mediainfo_data.get('videoCodec'):
            video_info.append(f"Codec: {mediainfo_data['videoCodec']}")
        if mediainfo_data.get('videoBitRate'):
            video_info.append(f"Bitrate: {self.format_bitrate(mediainfo_data['videoBitRate'])}")
        if mediainfo_data.get('videoFrameRate'):
            video_info.append(f"Frame Rate: {mediainfo_data['videoFrameRate']} FPS")
        if mediainfo_data.get('videoBitDepth'):
            video_info.append(f"Bit Depth: {mediainfo_data['videoBitDepth']} Bit")
        
        if video_info:
            embed.add_field(name="Video", value="\n".join(video_info), inline=False)
        
        # Format Audio information
        audio_tracks = mediainfo_data.get('audioTracks', [])
        if audio_tracks:
            audio_info = []
            for track in audio_tracks:
                track_info = []
                if track.get('language'):
                    track_info.append(track['language'])
                if track.get('codec'):
                    track_info.append(track['codec'])
                if track.get('channels'):
                    track_info.append(f"{track['channels']}ch")
                if track.get('bitRate'):
                    track_info.append(f"@{self.format_bitrate(track['bitRate'])}")
                if track.get('isDefault'):
                    track_info.append("Default")
                
                audio_info.append(" ".join(track_info))
            
            embed.add_field(name="Audio", value="\n".join(audio_info), inline=False)
        
        # Format Subtitle information
        subtitle_tracks = mediainfo_data.get('subtitleTracks', [])
        if subtitle_tracks:
            subtitle_info = []
            for track in subtitle_tracks:
                track_info = []
                if track.get('language'):
                    track_info.append(track['language'])
                if track.get('forced'):
                    track_info.append("Forced")
                if track.get('format'):
                    track_info.append(track['format'])
                if track.get('isDefault'):
                    track_info.append("Default")
                
                subtitle_info.append(" ".join(track_info))
            
            embed.add_field(name="Subtitles", value="\n".join(subtitle_info), inline=False)
        
        # Add source field
        embed.add_field(name="Source", value="[crowdNFO](https://crowdnfo.net/)", inline=False)
        
        embed.set_footer(text="Info: keine NFO gefunden, crowdNFO MediaInfo Fallback")
        
        # Create view with button
        view = View()
        view.add_item(api_responses['crowdnfo']['button'])
        
        await ctx.send(embed=embed, view=view)

    def format_file_size(self, size_bytes):
        """Format file size in bytes to human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"

    def format_duration(self, seconds):
        """Format duration in seconds to human readable format"""
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        
        if hours > 0:
            return f"{hours}h {minutes:02d}min {seconds:02d}s"
        else:
            return f"{minutes}min {seconds:02d}s"

    def format_bitrate(self, bitrate):
        """Format bitrate in bps to kbps or mbps"""
        if bitrate >= 1000000:
            return f"{bitrate/1000000:.1f} mbps"
        else:
            return f"{bitrate/1000:.0f} kbps"

    async def fetch_comments(self, release, data):
        params = {
            "dirname": {release}
        }

        if data['nfo_type'] == "release":
            comments_url = f"{self.xrel_api_base_url}/{data['nfo_type']}/info.json"
        else:
            comments_url = f"{self.xrel_api_base_url}/p2p/rls_info.json"

        comments_response = requests.get(comments_url, params=params)

        comments = comments_response.json()['comments']

        return f"[{comments}]({data['release_url']})"

    async def send_embed_with_image(self, ctx, file_path, file_name, view, source, release_type, color, comments="0"):
        embed = discord.Embed(
            title=f"{file_name}",
            color=color
        )

        embed.set_image(url=f"attachment://{file_name}.png")

        embed.add_field(name="Comments", value=comments, inline=True)
        embed.add_field(name="Release Type", value=release_type, inline=True)
        embed.add_field(name="Source", value=source, inline=False)

        with open(file_path + '.png', "rb") as fp:
            await ctx.send(
                file=discord.File(fp, f"{file_name}.png"),
                embed=embed,
                view=view,
            )

    # XRel token oauth zeugs
    def load_credentials(self):
        script_dir = os.path.dirname(__file__)
        env_path = os.path.join(script_dir, ".env")
        if not os.path.exists(env_path):
            print(
                f"No .env file found at {env_path}. Ensure the .env file is in the correct directory."
            )
            return None, None

        with open(env_path, "r") as file:
            lines = file.read().splitlines()
            credentials = {
                line.split("=")[0].strip(): line.split("=")[1].strip() for line in lines
            }
        return credentials.get("CLIENT_ID"), credentials.get("CLIENT_SECRET")

    async def get_token(self):
        """Fetches or reuses the OAuth2 token using Client Credentials Grant with curl."""
        current_time = asyncio.get_event_loop().time()
        logging.debug(f"Current time: {current_time}")
        if not self.token or current_time >= self.token_expires_at:
            curl_command = [
                "curl",
                "-X", "POST",
                f"{self.xrel_api_base_url}/oauth2/token",
                "--data", "grant_type=client_credentials",
                "--data", "scope=viewnfo",
                "--user", f"{self.client_id}:{self.client_secret}"
            ]

            try:
                result = subprocess.run(curl_command, capture_output=True, text=True)
                logging.debug(f"Curl stdout: {result.stdout}")
                logging.debug(f"Curl stderr: {result.stderr}")

                if result.returncode == 0:
                    token_data = json.loads(result.stdout)
                    self.token = token_data.get("access_token")
                    expires_in = token_data.get("expires_in", 3600)
                    self.token_expires_at = current_time + expires_in - 60  # Refresh 1 minute before expiration
                    logging.debug(f"Token: {self.token}")
                    logging.debug(f"Token expires at: {self.token_expires_at}")
                    if not self.token or self.token.count(".") != 2:
                        logging.error("Invalid token format: %s", self.token)
                        self.token = None  # Reset token if invalid
                else:
                    logging.error(f"Failed to retrieve token: {result.stderr}")
                    self.token = None
            except Exception as e:
                logging.error(f"Error occurred during curl command: {e}")
                self.token = None
        return self.token

    async def schedule_token_refresh(self):
        """Schedule token refresh every hour."""
        while True:
            await self.get_token()
            await asyncio.sleep(3600)  # Sleep for 1 hour

    def setup(bot):
        bot.add_cog(getnfo(bot))
