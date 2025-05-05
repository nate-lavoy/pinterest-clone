from django.contrib import messages
from django.shortcuts import render, redirect
from django.http import HttpResponseNotFound, HttpResponse
from django.db import connection
import requests
from psycopg2 import Binary

def download_image_as_blob(image_url):
    "download image as a blob--HELPER FUNCTION"
    response = requests.get(image_url, timeout=10)
    response.raise_for_status()  # Throw error if not 200
    return Binary(response.content)  # For PostgreSQL BYTEA

def dictfetchall(cursor):
    "Return all rows from a cursor as a list of dicts--HELPER FUNCTION"
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def home(request):
    return redirect('pinboards')

def signup(request):
    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        bio = request.POST.get('profile_bio', '')

        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO AppUser (username, email, password, profile_bio)
                VALUES (%s, %s, %s, %s)
            """, [username, email, password, bio])

        return redirect('login')  

    return render(request, 'pins/signup.html')

def login_view(request):
    if request.method == 'POST':
        identifier = request.POST['identifier']  # could be email or username
        password = request.POST['password']

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT user_id, username, password
                FROM AppUser
                WHERE email = %s OR username = %s
            """, [identifier, identifier])
            row = cursor.fetchone()

        if row is None:
            messages.error(request, 'User not found')
            return redirect('login')

        user_id, username, stored_password = row

        if stored_password != password:
            messages.error(request, 'Incorrect password')
            return redirect('login')

        # Log the user in
        request.session['user_id'] = user_id
        request.session['username'] = username

        return redirect('pinboards')  # Redirect to pinboards after login

    return render(request, 'pins/login.html')

def logout_view(request):
    request.session.flush()  # Clears all session data
    return redirect('login')

def edit_profile(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    if request.method == 'POST':
        new_bio = request.POST['profile_bio']

        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE AppUser
                SET profile_bio = %s
                WHERE user_id = %s
            """, [new_bio, user_id])

        return redirect('pinboards')

    # If GET, fetch current profile_bio to prefill the form
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT profile_bio
            FROM AppUser
            WHERE user_id = %s
        """, [user_id])
        row = cursor.fetchone()

    current_bio = row[0] if row else ''

    return render(request, 'pins/edit_profile.html', {'current_bio': current_bio})

def pinboards(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT b.board_id, b.board_name, b.comment_permission,
                   ARRAY(
                       SELECT pic.original_url
                       FROM Pin p
                       JOIN Picture pic ON p.picture_id = pic.picture_id
                       WHERE p.board_id = b.board_id
                       ORDER BY p.pinned_at DESC
                       LIMIT 3
                   ) AS thumbnails
            FROM Pinboard b
            WHERE b.user_id = %s
        """, [user_id])
        boards = dictfetchall(cursor)

    return render(request, 'pins/pinboards.html', {'boards': boards})

def create_pinboard(request):
    if request.method == 'POST':
        user_id = request.session.get('user_id')
        if not user_id:
            return redirect('login')

        board_name = request.POST['board_name']
        comment_permission = request.POST['comment_permission']

        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO Pinboard (user_id, board_name, comment_permission)
                VALUES (%s, %s, %s)
            """, [user_id, board_name, comment_permission])

        return redirect('pinboards')

    return render(request, 'pins/create_pinboard.html')

def pin_picture(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    if request.method == 'POST':
        board_id = request.POST.get('board_id')
        original_url = request.POST.get('original_url')
        page_url = request.POST.get('page_url')
        tags = request.POST.get('tags')

        with connection.cursor() as cursor:
            # Insert the picture into the Picture table
            cursor.execute("""
                INSERT INTO Picture (original_url, page_url, tags)
                VALUES (%s, %s, %s)
                RETURNING picture_id
            """, [original_url, page_url, tags])
            picture_id = cursor.fetchone()[0]

            # Insert the pin into the Pin table
            cursor.execute("""
                INSERT INTO Pin (board_id, picture_id, pinned_by, is_repin, original_pin_id)
                VALUES (%s, %s, %s, FALSE, NULL)
            """, [board_id, picture_id, user_id])

        # Redirect to the board the picture was pinned to
        return redirect('view_pinboard', board_id=board_id)

    # Fetch the user's boards for the dropdown
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT board_id, board_name
            FROM Pinboard
            WHERE user_id = %s
        """, [user_id])
        boards = dictfetchall(cursor)

    return render(request, 'pins/pin_picture.html', {'boards': boards})

def serve_blob_image(request, picture_id):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT image_blob FROM Picture WHERE picture_id = %s
        """, [picture_id])
        row = cursor.fetchone()
        if not row or not row[0]:
            return HttpResponseNotFound()

        image_data = row[0]
        return HttpResponse(image_data, content_type="image/jpeg")


def view_pinboard(request, board_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    with connection.cursor() as cursor:
        # Fetch board details, including comment_permission
        cursor.execute("""
            SELECT b.board_name, u.username AS created_by, u.user_id AS created_by_id, b.comment_permission
            FROM Pinboard b
            JOIN AppUser u ON b.user_id = u.user_id
            WHERE b.board_id = %s
        """, [board_id])
        board = cursor.fetchone()

        if not board:
            return HttpResponseNotFound("Board not found")

        board_name, created_by, created_by_id, comment_permission = board

        # Fetch pictures for the board
        cursor.execute("""
            SELECT p.pin_id, pic.picture_id, pic.original_url, pic.tags, 
                   COUNT(pl.user_id) AS like_count, 
                   p.pinned_at, 
                   (SELECT COUNT(*) FROM Pin WHERE original_pin_id = p.pin_id) AS repin_count
            FROM Pin p
            JOIN Picture pic ON p.picture_id = pic.picture_id
            LEFT JOIN PictureLike pl ON pic.picture_id = pl.picture_id
            WHERE p.board_id = %s
            GROUP BY p.pin_id, pic.picture_id, pic.original_url, pic.tags
            ORDER BY p.pinned_at DESC
        """, [board_id])
        pictures = dictfetchall(cursor)

        # Fetch comments for the pins
        pin_ids = [pic['pin_id'] for pic in pictures]
        comments = []
        if pin_ids:
            format_strings = ','.join(['%s'] * len(pin_ids))
            cursor.execute(f"""
                SELECT c.comment_id, c.pin_id, u.username, c.comment_text, c.commented_at
                FROM Comment c
                JOIN AppUser u ON c.user_id = u.user_id
                WHERE c.pin_id IN ({format_strings})
                ORDER BY c.commented_at ASC
            """, pin_ids)
            comments = dictfetchall(cursor)

    return render(request, 'pins/view_pinboard.html', {
        'board_name': board_name,
        'created_by': created_by,
        'created_by_id': created_by_id,
        'board_id': board_id,
        'pictures': pictures,
        'comments': comments,  # Pass comments to the template
        'comment_permission': comment_permission,
    })

def repin(request, pin_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    if request.method == 'POST':
        board_id = request.POST['board_id']

        # Validate that the board belongs to the current user
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 1
                FROM Pinboard
                WHERE board_id = %s AND user_id = %s
            """, [board_id, user_id])
            board_exists = cursor.fetchone()

        if not board_exists:
            messages.error(request, 'Invalid board selection.')
            return redirect('pinboards')

        # Get the root pin's picture_id and original_pin_id
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT picture_id, COALESCE(original_pin_id, pin_id) AS root_pin_id
                FROM Pin
                WHERE pin_id = %s
            """, [pin_id])
            row = cursor.fetchone()

        if not row:
            messages.error(request, 'The pin you are trying to repin does not exist.')
            return redirect('pinboards')

        picture_id, root_pin_id = row

        # Create the repin
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO Pin (board_id, picture_id, pinned_by, is_repin, original_pin_id)
                VALUES (%s, %s, %s, TRUE, %s)
            """, [board_id, picture_id, user_id, root_pin_id])

        messages.success(request, 'Pin successfully repinned!')
        return redirect('pinboards')

    # If GET: show form to pick a board
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT board_id, board_name
            FROM Pinboard
            WHERE user_id = %s
        """, [user_id])
        boards = dictfetchall(cursor)

    return render(request, 'pins/repin.html', {'boards': boards})


def like_picture(request, pin_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    if request.method == 'POST':
        with connection.cursor() as cursor:
            # get picture_id and board_id for redirect
            cursor.execute("SELECT picture_id, board_id FROM Pin WHERE pin_id = %s", [pin_id])
            row = cursor.fetchone()
            if not row:
                return redirect('pinboards')
            picture_id, board_id = row

            # Check if already liked
            cursor.execute("""
                SELECT 1 FROM PictureLike
                WHERE user_id = %s AND picture_id = %s
            """, [user_id, picture_id])
            liked = cursor.fetchone()

            if liked:
                # Unlike
                cursor.execute("""
                    DELETE FROM PictureLike
                    WHERE user_id = %s AND picture_id = %s
                """, [user_id, picture_id])
            else:
                # Like
                cursor.execute("""
                    INSERT INTO PictureLike (user_id, picture_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                """, [user_id, picture_id])

        referer = request.META.get('HTTP_REFERER', '/')
        return redirect(f"{referer}#pin-{pin_id}")


def comment_on_pin(request, pin_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    if request.method == 'POST':
        comment_text = request.POST['comment_text']

        with connection.cursor() as cursor:
            # 1. Execute the SELECT
            cursor.execute("""
                SELECT pb.comment_permission, pb.board_id
                FROM Pin p
                JOIN Pinboard pb ON p.board_id = pb.board_id
                WHERE p.pin_id = %s
            """, [pin_id])

            # 2. Immediately fetch
            result = cursor.fetchone()

        # 3. NOW check if result is None
        if result is None:
            return redirect('pinboards')  # If not found, redirect safely

        # 4. Only unpack AFTER checking
        comment_permission, board_id = result

        # 5. Check permission
        allowed = False
        if comment_permission == 'all':
            allowed = True
        elif comment_permission == 'none':
            allowed = False
        elif comment_permission == 'friends':
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT 1
                    FROM Friendship
                    WHERE user_id = (SELECT user_id FROM Pinboard WHERE board_id = %s)
                      AND friend_id = %s
                      AND status = 'accepted'
                """, [board_id, user_id])
                friendship = cursor.fetchone()
                if friendship:
                    allowed = True

        # 6. Insert the comment if allowed
        if allowed:
            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO Comment (user_id, pin_id, comment_text)
                    VALUES (%s, %s, %s)
                """, [user_id, pin_id, comment_text])

        referer = request.META.get('HTTP_REFERER', '/')
        return redirect(f"{referer}#pin-{pin_id}")


def find_users(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT u.user_id, u.username
            FROM AppUser u
            WHERE u.user_id != %s
              AND u.user_id NOT IN (
                  SELECT friend_id FROM Friendship WHERE user_id = %s
                  UNION
                  SELECT user_id FROM Friendship WHERE friend_id = %s
              )
        """, [user_id, user_id, user_id])

        users = dictfetchall(cursor)

    return render(request, 'pins/find_users.html', {'users': users})


def send_friend_request(request, friend_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    with connection.cursor() as cursor:
        cursor.execute("""
            INSERT INTO Friendship (user_id, friend_id, status)
            VALUES (%s, %s, 'pending')
            ON CONFLICT DO NOTHING
        """, [user_id, friend_id])

    return redirect('find_users')

def pending_requests(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    with connection.cursor() as cursor:
        # Find all incoming pending requests
        cursor.execute("""
            SELECT f.user_id, u.username
            FROM Friendship f
            JOIN AppUser u ON f.user_id = u.user_id
            WHERE f.friend_id = %s AND f.status = 'pending'
        """, [user_id])
        requests = dictfetchall(cursor)

    return render(request, 'pins/pending_requests.html', {'requests': requests})

def respond_request(request, requester_id, response):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    if response == 'accept':
        new_status = 'accepted'
    elif response == 'decline':
        new_status = 'declined'
    else:
        return redirect('pending_requests')

    with connection.cursor() as cursor:
        # Update the status
        cursor.execute("""
            UPDATE Friendship
            SET status = %s
            WHERE user_id = %s AND friend_id = %s
        """, [new_status, requester_id, user_id])

        if new_status == 'accepted':
            # Create reciprocal friendship
            cursor.execute("""
                INSERT INTO Friendship (user_id, friend_id, status)
                VALUES (%s, %s, 'accepted')
                ON CONFLICT DO NOTHING
            """, [user_id, requester_id])

    return redirect('pending_requests')

def my_friends(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    with connection.cursor() as cursor:
        # Find all accepted friendships
        cursor.execute("""
            SELECT u.user_id, u.username
            FROM Friendship f
            JOIN AppUser u ON f.friend_id = u.user_id
            WHERE f.user_id = %s AND f.status = 'accepted'
        """, [user_id])
        friends = dictfetchall(cursor)

    return render(request, 'pins/my_friends.html', {'friends': friends})

def my_follow_streams(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT fs.stream_id, fs.stream_name,
                   ARRAY(
                       SELECT pic.original_url
                       FROM FollowedBoard fb
                       JOIN Pin p ON fb.board_id = p.board_id
                       JOIN Picture pic ON p.picture_id = pic.picture_id
                       WHERE fb.stream_id = fs.stream_id
                       ORDER BY p.pinned_at DESC
                       LIMIT 3
                   ) AS thumbnails
            FROM FollowStream fs
            WHERE fs.user_id = %s
        """, [user_id])
        streams = dictfetchall(cursor)

    return render(request, 'pins/my_follow_streams.html', {'streams': streams})


def create_follow_stream(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    if request.method == 'POST':
        stream_name = request.POST.get('stream_name')

        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO FollowStream (user_id, stream_name)
                VALUES (%s, %s)
            """, [user_id, stream_name])

        return redirect('my_follow_streams')

    return render(request, 'pins/create_follow_stream.html')


def add_board_to_stream(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    if request.method == 'POST':
        board_id = request.POST.get('board_id')
        stream_id = request.POST.get('stream_id')

        with connection.cursor() as cursor:
            # Add the board to the selected stream
            cursor.execute("""
                INSERT INTO FollowedBoard (stream_id, board_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, [stream_id, board_id])

        return redirect('view_follow_stream', stream_id=stream_id)

    return redirect('my_follow_streams')


def view_follow_stream(request, stream_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    with connection.cursor() as cursor:
        # Get the stream name
        cursor.execute("""
            SELECT stream_name
            FROM FollowStream
            WHERE stream_id = %s AND user_id = %s
        """, [stream_id, user_id])
        row = cursor.fetchone()
        if not row:
            return redirect('my_follow_streams')
        stream_name = row[0]

        # Get boards in this stream
        cursor.execute("""
            SELECT pb.board_id, pb.board_name
            FROM FollowedBoard fb
            JOIN Pinboard pb ON fb.board_id = pb.board_id
            WHERE fb.stream_id = %s
        """, [stream_id])
        boards = dictfetchall(cursor)

        # Get pins from boards in this stream
        cursor.execute("""
            SELECT p.pin_id, pic.picture_id, pic.original_url, pic.tags, p.board_id,
                   COUNT(pl.user_id) AS like_count,
                   MAX(CASE WHEN pl.user_id = %s THEN 1 ELSE 0 END) AS user_liked,
                   (SELECT COUNT(*) 
                    FROM Pin 
                    WHERE original_pin_id = COALESCE(p.original_pin_id, p.pin_id)) AS repin_count,
                   MAX(CASE WHEN p.pinned_by = %s THEN 1 ELSE 0 END) AS user_repin
            FROM FollowedBoard fb
            JOIN Pinboard pb ON fb.board_id = pb.board_id
            JOIN Pin p ON p.board_id = pb.board_id
            JOIN Picture pic ON p.picture_id = pic.picture_id
            LEFT JOIN PictureLike pl ON pic.picture_id = pl.picture_id
            WHERE fb.stream_id = %s
            GROUP BY p.pin_id, pic.picture_id, pic.original_url, pic.tags, p.board_id
            ORDER BY p.pinned_at DESC
        """, [user_id, user_id, stream_id])
        pictures = dictfetchall(cursor)

        # Pull all comments for these pins
        pin_ids = [pic['pin_id'] for pic in pictures]
        comments = []
        if pin_ids:
            format_strings = ','.join(['%s'] * len(pin_ids))
            cursor.execute(f"""
                SELECT c.comment_id, c.pin_id, u.username, c.comment_text, c.commented_at
                FROM Comment c
                JOIN AppUser u ON c.user_id = u.user_id
                WHERE c.pin_id IN ({format_strings})
                ORDER BY c.commented_at ASC
            """, pin_ids)
            comments = dictfetchall(cursor)

    return render(request, 'pins/view_follow_stream.html', {
        'board_name': stream_name,
        'boards': boards,  # Pass the boards in the stream
        'pictures': pictures,
        'comments': comments,
        'board_id': None,  # Mark that this isn't a specific board
        'stream_id': stream_id,
        'show_board_button': True
    })

def remove_board_from_stream(request, stream_id, board_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    with connection.cursor() as cursor:
        # Ensure the user owns the stream
        cursor.execute("""
            SELECT 1
            FROM FollowStream
            WHERE stream_id = %s AND user_id = %s
        """, [stream_id, user_id])
        stream_exists = cursor.fetchone()

        if not stream_exists:
            messages.error(request, 'You do not have permission to modify this stream.')
            return redirect('my_follow_streams')

        # Remove the board from the stream
        cursor.execute("""
            DELETE FROM FollowedBoard
            WHERE stream_id = %s AND board_id = %s
        """, [stream_id, board_id])

    messages.success(request, 'Board successfully removed from the stream.')
    return redirect('view_follow_stream', stream_id=stream_id)

def delete_follow_stream(request, stream_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    if request.method == 'POST':
        with connection.cursor() as cursor:
            # Ensure the user owns the stream
            cursor.execute("""
                SELECT 1
                FROM FollowStream
                WHERE stream_id = %s AND user_id = %s
            """, [stream_id, user_id])
            stream_exists = cursor.fetchone()

            if not stream_exists:
                messages.error(request, 'You do not have permission to delete this stream.')
                return redirect('my_follow_streams')

            # Delete the follow stream (cascades to FollowedBoard)
            cursor.execute("""
                DELETE FROM FollowStream
                WHERE stream_id = %s AND user_id = %s
            """, [stream_id, user_id])

        messages.success(request, 'Follow stream removed successfully.')
    return redirect('my_follow_streams')

def delete_pin(request, pin_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    with connection.cursor() as cursor:
        # Make sure the pin belongs to this user
        cursor.execute("SELECT board_id FROM Pin WHERE pin_id = %s AND pinned_by = %s", [pin_id, user_id])
        result = cursor.fetchone()

        if result:
            board_id = result[0]
            cursor.execute("DELETE FROM Pin WHERE pin_id = %s", [pin_id])
            return redirect(f'/pinboard/{board_id}/')

    return redirect('pinboards')

def delete_pinboard(request, board_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    if request.method == 'POST':
        with connection.cursor() as cursor:
            # Ensure the user owns the pinboard
            cursor.execute("""
                SELECT 1
                FROM Pinboard
                WHERE board_id = %s AND user_id = %s
            """, [board_id, user_id])
            board_exists = cursor.fetchone()

            if not board_exists:
                messages.error(request, 'You do not have permission to delete this pinboard.')
                return redirect('pinboards')

            # Delete the pinboard
            cursor.execute("""
                DELETE FROM Pinboard
                WHERE board_id = %s AND user_id = %s
            """, [board_id, user_id])

        messages.success(request, 'Pinboard removed successfully.')
    return redirect('pinboards')

def keyword_search(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    query = request.GET.get('q', '').strip()
    pictures = []
    boards = []
    if query:
        with connection.cursor() as cursor:
            # Search for pictures
            cursor.execute("""
                SELECT DISTINCT p.pin_id, pic.picture_id, pic.original_url, pic.tags, p.board_id, 
                                COUNT(pl.user_id) AS like_count, 
                                p.pinned_at, 
                                (SELECT COUNT(*) FROM Pin WHERE original_pin_id = p.pin_id) AS repin_count
                FROM Pin p
                JOIN Picture pic ON p.picture_id = pic.picture_id
                LEFT JOIN PictureLike pl ON pic.picture_id = pl.picture_id
                WHERE pic.tags ILIKE %s
                AND p.original_pin_id IS NULL
                GROUP BY p.pin_id, pic.picture_id, pic.original_url, pic.tags, p.board_id
                ORDER BY p.pinned_at DESC
            """, [f'%{query}%'])
            pictures = dictfetchall(cursor)

            # Search for boards with thumbnails
            cursor.execute("""
                SELECT b.board_id, b.board_name, b.comment_permission, u.username AS created_by, u.user_id AS created_by_id,
                       ARRAY(
                           SELECT pic.original_url
                           FROM Pin p
                           JOIN Picture pic ON p.picture_id = pic.picture_id
                           WHERE p.board_id = b.board_id
                           ORDER BY p.pinned_at DESC
                           LIMIT 3
                       ) AS thumbnails
                FROM Pinboard b
                JOIN AppUser u ON b.user_id = u.user_id
                WHERE b.board_name ILIKE %s
                ORDER BY b.board_name ASC
            """, [f'%{query}%'])
            boards = dictfetchall(cursor)

    return render(request, 'pins/search_results.html', {
        'query': query,
        'pictures': pictures,
        'boards': boards,
        'show_board_button': True
    })

def profile(request, user_id):
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return redirect('login')

    with connection.cursor() as cursor:
        # Fetch user information
        cursor.execute("""
            SELECT username, profile_bio
            FROM AppUser
            WHERE user_id = %s
        """, [user_id])
        user_info = cursor.fetchone()

        if not user_info:
            return HttpResponseNotFound("User not found")

        username, profile_bio = user_info

        # Fetch user's boards with thumbnails
        cursor.execute("""
            SELECT b.board_id, b.board_name, b.comment_permission,
                   ARRAY(
                       SELECT pic.original_url
                       FROM Pin p
                       JOIN Picture pic ON p.picture_id = pic.picture_id
                       WHERE p.board_id = b.board_id
                       ORDER BY p.pinned_at DESC
                       LIMIT 3
                   ) AS thumbnails
            FROM Pinboard b
            WHERE b.user_id = %s
        """, [user_id])
        boards = dictfetchall(cursor)

        # Check if the current user is already friends with this user
        cursor.execute("""
            SELECT 1
            FROM Friendship
            WHERE (user_id = %s AND friend_id = %s AND status = 'accepted')
               OR (user_id = %s AND friend_id = %s AND status = 'accepted')
        """, [current_user_id, user_id, user_id, current_user_id])
        is_friend = cursor.fetchone() is not None

    return render(request, 'pins/profile.html', {
        'username': username,
        'profile_bio': profile_bio,
        'boards': boards,
        'is_friend': is_friend,
        'user_id': user_id,
        'current_user_id': current_user_id,
    })

