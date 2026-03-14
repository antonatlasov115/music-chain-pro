"""
Playlist Generator Module for Auto-DJ Phase 3

This module handles playlist generation and file creation:
- Interactive song selection from music library
- Playlist generation based on similarity analysis
- M3U playlist file creation for music players
- User-friendly interface for playlist creation
"""

import os
import json
from typing import List, Dict, Tuple, Optional
from pathlib import Path
import logging

from .library_manager import LibraryManager
from .audio_processor import AudioProcessor

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PlaylistGenerator:
    """
    Main class for generating playlists from music libraries.
    
    This class implements the Phase 3 functionality of the Auto-DJ project:
    1. User Interaction: Load library and present song list
    2. Song Selection: Allow user to choose seed song and playlist length
    3. Playlist Generation: Use similarity engine to find similar songs
    4. File Creation: Generate M3U playlist files for music players
    """
    
    def __init__(self, 
                 library_manager: Optional[LibraryManager] = None,
                 audio_processor: Optional[AudioProcessor] = None):
        """
        Initialize the PlaylistGenerator.
        
        Args:
            library_manager: LibraryManager instance for similarity analysis
            audio_processor: AudioProcessor instance for audio processing
        """
        self.audio_processor = audio_processor or AudioProcessor()
        self.library_manager = library_manager or LibraryManager(self.audio_processor)
        self.current_library_path = None
        
    def load_library_from_path(self, library_path: str, force_rebuild: bool = True) -> bool:
        try:
            auto_dj_path = os.path.join(library_path, 'auto-dj')
            database_path = os.path.join(auto_dj_path, 'music_library.db')
            
            logger.info("Building library from current folder contents...")
            
            self.library_manager = LibraryManager(self.audio_processor, database_path)
            stats = self.library_manager.build_library(library_path, recursive=True, force_rebuild=force_rebuild)
            
            #  ИСПРАВЛЕНИЕ: Теперь бот понимает оба варианта ответа от базы
            song_count = stats.get('processed_songs', stats.get('total_songs', 0))
            
            if song_count == 0:
                logger.error("No songs could be processed from the library")
                return False
                
            logger.info(f"Built/Loaded library with {song_count} songs")
            self.current_library_path = library_path
            return True
            
        except Exception as e:
            logger.error(f"Error loading library from {library_path}: {str(e)}")
            return False
    
    def get_song_list_for_selection(self) -> List[Tuple[int, str, Dict]]:
        """
        Get a formatted list of songs for user selection.
        
        Returns:
            List of tuples: (index, filename, metadata)
        """
        if not self.library_manager.library_data:
            logger.warning("No library data available")
            return []
        
        songs = self.library_manager.get_song_list()
        formatted_songs = []
        
        for i, (file_path, metadata) in enumerate(songs, 1):
            formatted_songs.append((i, metadata['filename'], metadata))
        
        return formatted_songs
    
    def generate_playlist(self, seed_song_index: int, playlist_length: int, 
                         exclude_seed: bool = True) -> List[Tuple[str, float, Dict]]:
        """
        Generate a playlist based on a seed song.
        
        Args:
            seed_song_index: Index of the seed song (1-based)
            playlist_length: Number of songs in the playlist
            exclude_seed: Whether to exclude the seed song from the playlist
            
        Returns:
            List of tuples: (file_path, similarity_score, metadata)
        """
        try:
            songs = self.library_manager.get_song_list()
            
            if seed_song_index < 1 or seed_song_index > len(songs):
                raise ValueError(f"Invalid song index: {seed_song_index}")
            
            # Get seed song
            seed_file_path = songs[seed_song_index - 1][0]
            seed_metadata = songs[seed_song_index - 1][1]
            
            logger.info(f"Generating playlist based on: {seed_metadata['filename']}")
            
            # Find similar songs
            recommendations = self.library_manager.find_similar_songs(
                seed_file_path, 
                top_n=playlist_length + (1 if exclude_seed else 0),
                include_seed=not exclude_seed
            )
            
            # Filter out seed song if requested
            if exclude_seed:
                recommendations = [(fp, sim, meta) for fp, sim, meta in recommendations 
                                 if fp != seed_file_path]
            
            # Limit to requested length
            playlist = recommendations[:playlist_length]
            
            logger.info(f"Generated playlist with {len(playlist)} songs")
            return playlist
            
        except Exception as e:
            logger.error(f"Error generating playlist: {str(e)}")
            raise
    
    def create_m3u_playlist(self, playlist: List[Tuple[str, float, Dict]], 
                          output_path: str, playlist_name: str = "auto_dj_playlist") -> str:
        """
        Create an M3U playlist file from the generated playlist.
        
        Args:
            playlist: List of playlist songs (file_path, similarity, metadata)
            output_path: Directory where to save the playlist file
            playlist_name: Name of the playlist file (without extension)
            
        Returns:
            Path to the created M3U file
        """
        try:
            # Create M3U file directly in the music library folder
            m3u_filename = f"{playlist_name}.m3u"
            m3u_path = os.path.join(output_path, m3u_filename)
            
            # Write M3U file
            with open(m3u_path, 'w', encoding='utf-8') as f:
                # Write M3U header
                f.write("#EXTM3U\n")
                f.write(f"# Generated by Auto-DJ Phase 3\n")
                f.write(f"# Playlist: {playlist_name}\n")
                f.write(f"# Songs: {len(playlist)}\n\n")
                
                # Write playlist entries
                for i, (file_path, similarity, metadata) in enumerate(playlist, 1):
                    # M3U extended format entry
                    duration_seconds = int(metadata['duration_seconds'])
                    filename = metadata['filename']
                    
                    # Convert to absolute path for better compatibility
                    absolute_path = os.path.abspath(file_path)
                    # Use forward slashes for better media player compatibility
                    absolute_path = absolute_path.replace('\\', '/')
                    
                    f.write(f"#EXTINF:{duration_seconds},{filename}\n")
                    f.write(f"{absolute_path}\n")
            
            logger.info(f"Created M3U playlist: {m3u_path}")
            return m3u_path
            
        except Exception as e:
            logger.error(f"Error creating M3U playlist: {str(e)}")
            raise
    
    def create_playlist_with_metadata(self, playlist: List[Tuple[str, float, Dict]], 
                                    output_path: str, playlist_name: str = "auto_dj_playlist") -> Dict:
        """
        Create both M3U playlist and metadata JSON file.
        
        Args:
            playlist: List of playlist songs (file_path, similarity, metadata)
            output_path: Directory where to save the files
            playlist_name: Name of the playlist files (without extension)
            
        Returns:
            Dictionary with paths to created files and playlist metadata
        """
        try:
            # Create M3U file
            m3u_path = self.create_m3u_playlist(playlist, output_path, playlist_name)
            
            # Create metadata JSON file directly in the music library folder
            json_filename = f"{playlist_name}_metadata.json"
            json_path = os.path.join(output_path, json_filename)
            
            playlist_metadata = {
                "playlist_name": playlist_name,
                "total_songs": len(playlist),
                "total_duration_minutes": sum(meta['duration_minutes'] for _, _, meta in playlist),
                "songs": []
            }
            
            # Add song details
            for i, (file_path, similarity, metadata) in enumerate(playlist, 1):
                song_info = {
                    "position": i,
                    "file_path": file_path,
                    "filename": metadata['filename'],
                    "duration_seconds": metadata['duration_seconds'],
                    "duration_minutes": metadata['duration_minutes'],
                    "similarity_score": similarity,
                    "sample_rate": metadata['sample_rate']
                }
                playlist_metadata["songs"].append(song_info)
            
            # Save metadata JSON
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(playlist_metadata, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Created playlist metadata: {json_path}")
            
            return {
                "m3u_path": m3u_path,
                "metadata_path": json_path,
                "playlist_metadata": playlist_metadata
            }
            
        except Exception as e:
            logger.error(f"Error creating playlist with metadata: {str(e)}")
            raise
    
    def get_library_info(self) -> Dict:
        """
        Get information about the current library.
        
        Returns:
            Dictionary containing library statistics and information
        """
        if not self.library_manager.library_data:
            return {"error": "No library loaded"}
        
        stats = self.library_manager.get_library_stats()
        songs = self.library_manager.get_song_list()
        
        return {
            "library_path": self.current_library_path,
            "total_songs": stats['total_songs'],
            "total_duration_minutes": stats['total_duration_minutes'],
            "total_duration_hours": stats['total_duration_hours'],
            "average_duration_minutes": stats['average_duration_minutes'],
            "songs": [{"filename": meta['filename'], "duration_minutes": meta['duration_minutes']} 
                     for _, meta in songs]
        }
    
    def validate_playlist_files(self, playlist: List[Tuple[str, float, Dict]]) -> Dict:
        """
        Validate that all files in the playlist exist and are accessible.
        
        Args:
            playlist: List of playlist songs
            
        Returns:
            Dictionary with validation results
        """
        validation_results = {
            "total_files": len(playlist),
            "valid_files": 0,
            "invalid_files": 0,
            "invalid_file_details": []
        }
        
        for file_path, similarity, metadata in playlist:
            if os.path.exists(file_path) and os.path.isfile(file_path):
                validation_results["valid_files"] += 1
            else:
                validation_results["invalid_files"] += 1
                validation_results["invalid_file_details"].append({
                    "file_path": file_path,
                    "filename": metadata['filename'],
                    "reason": "File not found or not accessible"
                })
        
        validation_results["all_files_valid"] = validation_results["invalid_files"] == 0
        return validation_results
